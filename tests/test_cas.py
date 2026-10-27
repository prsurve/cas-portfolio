from __future__ import annotations

from datetime import date
from decimal import Decimal

import casparser
import pytest
from casparser.enums import FileType
from casparser.exceptions import CASParseError, IncorrectPasswordError

from portfolio.config import get_settings
from portfolio.importers import cas
from portfolio.store import Store

from conftest import equity as cas_equity

D = Decimal


# ---- mutual fund statements (CAMS / KFintech) --------------------------------------


def test_mf_statement_parsed(mf_data):
    st = cas.from_mf_data(mf_data)
    assert (st.issuer, st.kind, st.cas_type) == ("CAMS", "mf", "DETAILED")
    assert (st.period_from, st.period_to) == (date(2026, 4, 1), date(2026, 9, 30))
    assert len(st.mfs) == 3  # redeemed scheme dropped
    flexi = st.mfs[0]
    assert flexi["folio_masked"] == "****7/89"
    assert flexi["units"] == D("100.5") and flexi["cost"] == D(6000) and flexi["value"] == D("8040")
    assert flexi["nav_date"] == date(2026, 9, 30) and flexi["scheme_type"] == "EQUITY"
    assert len(st.transactions) == 2  # includes the redeemed scheme's history
    redemption = st.transactions[1]
    assert redemption["type"] == "REDEMPTION" and redemption["units"] == D(-25)
    assert st.warnings == [] and st.notes == []


def test_mf_statement_never_keeps_pan_or_full_folio(mf_data):
    st = cas.from_mf_data(mf_data)
    blob = repr(st)
    assert "ABCDE1234F" not in blob and "1234567/89" not in blob and "9999999999" not in blob


def test_mf_units_mismatch_warns(mf_data):
    s = mf_data.folios[0].schemes[0]
    mf_data.folios[0].schemes[0] = s.model_copy(update={"close_calculated": D("88")})
    assert "differ from the sum of transactions" in cas.from_mf_data(mf_data).warnings[0]


def test_mf_without_cost_gets_note(mf_data):
    for folio in mf_data.folios:
        for i, s in enumerate(folio.schemes):
            folio.schemes[i] = s.model_copy(update={"valuation": s.valuation.model_copy(update={"cost": None})})
    assert "no purchase cost" in cas.from_mf_data(mf_data).notes[0]


# ---- stock statements (NSDL / CDSL) ------------------------------------------------


def test_demat_statement_parsed(demat_data):
    st = cas.from_demat_data(demat_data)
    assert (st.issuer, st.kind, st.period_to) == ("CDSL", "demat", date(2026, 9, 30))
    assert [a.broker for a in st.accounts] == ["zerodha", "upstox", "hdfc", None]
    assert [a.depository for a in st.accounts] == ["CDSL", "CDSL", "NSDL", "CDSL"]
    zerodha = st.accounts[0]
    assert zerodha.client_id_masked == "****5678"
    assert (zerodha.stocks, zerodha.funds, zerodha.bonds) == (2, 1, 0)
    assert zerodha.imported_value == D(12 * 1450 + 5 * 3400 + 1000) == zerodha.statement_total
    assert zerodha.gap == 0
    etf = next(r for r in st.stocks if r["asset_type"] == "fund")
    assert (etf["isin"], etf["quantity"], etf["value"]) == ("INF000000001", D(10), D(1000))
    assert len(st.stocks) == 6
    assert "12345678" not in repr(st.stocks)
    assert st.warnings == []  # every account adds up
    notes = " ".join(st.notes)
    assert "Imported 2 mutual fund holding(s) in 2 folio(s)" in notes
    assert "Unknown broker for: SOME OTHER DP LTD" in notes


def test_account_gap_is_reported(demat_data):
    acct = demat_data.accounts[0]
    demat_data.accounts[0] = acct.model_copy(update={"balance": acct.balance + D("765432.10")})
    st = cas.from_demat_data(demat_data)
    assert st.accounts[0].gap == D("765432.10")
    [warning] = st.warnings
    assert "ZERODHA BROKING LIMITED ****5678" in warning
    assert "₹7,65,432.10 is missing" in warning and "--diagnose" in warning


def test_rights_entitlement_and_bonds_kept(demat_data):
    from casparser.types import Bond, Equity
    acct = demat_data.accounts[1]
    rights = Equity(name="INFY RIGHTS", isin="INE009A20018", num_shares=D(0), price=D(0), value=D(250))
    empty = Equity(name="EMPTY ROW", isin="INE009A20026", num_shares=D(0), price=D(0), value=D(0))
    bond = Bond(name="SGB 2028", isin="IN0020200047", num_bonds=D(4), value=D(28000), market_price=D(7000))
    demat_data.accounts[1] = acct.model_copy(update={
        "equities": acct.equities + [rights, empty], "bonds": [bond], "balance": acct.balance + D(28250),
    })
    st = cas.from_demat_data(demat_data)
    upstox = st.accounts[1]
    assert (upstox.stocks, upstox.bonds, upstox.gap) == (2, 1, 0)
    isins = {r["isin"] for r in st.stocks}
    assert "INE009A20018" in isins and "INE009A20026" not in isins
    sgb = next(r for r in st.stocks if r["asset_type"] == "bond")
    assert (sgb["quantity"], sgb["price"], sgb["value"]) == (D(4), D(7000), D(28000))


@pytest.mark.parametrize("value,text", [
    (D("1234567.89"), "₹12,34,567.89"), (D("543210.98"), "₹5,43,210.98"), (D("999"), "₹999.00"),
    (D("100000"), "₹1,00,000.00"), (D("-1500.5"), "-₹1,500.50"), (D(0), "₹0.00"),
])
def test_inr_grouping(value, text):
    assert cas.inr(value) == text


@pytest.mark.parametrize("dp,broker", [
    ("ZERODHA BROKING LIMITED", "zerodha"),
    ("RKSV SECURITIES INDIA PRIVATE LIMITED", "upstox"),
    ("Upstox Securities Pvt Ltd", "upstox"),
    ("HDFC BANK LIMITED", "hdfc"),
    ("MIRAE ASSET SHAREKHAN LIMITED", "sharekhan"),
    ("ANGEL ONE LIMITED", "angelone"),
    ("GROWW INVEST TECH PRIVATE LIMITED", "groww"),
    ("AXIS SECURITIES LIMITED", "axis"),
    ("SOME OTHER DP LTD", None),
    (None, None),
])
def test_map_broker(dp, broker):
    assert cas.map_broker(dp) == broker


@pytest.mark.parametrize("value,expected", [
    ("30-Sep-2026", date(2026, 9, 30)), ("30/09/2026", date(2026, 9, 30)), ("30-09-2026", date(2026, 9, 30)),
    (date(2026, 9, 30), date(2026, 9, 30)), ("", None), ("garbage", None), (None, None),
])
def test_parse_date(value, expected):
    assert cas.parse_date(value) == expected


# ---- parse_pdf_bytes: routing and errors ------------------------------------------


def test_routes_by_issuer(monkeypatch, mf_data, demat_data):
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda *a, **k: mf_data)
    assert cas.parse_pdf_bytes(b"%PDF", "pw").kind == "mf"
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda *a, **k: demat_data)
    assert cas.parse_pdf_bytes(b"%PDF", "pw").kind == "demat"


def test_password_retried_in_capitals(monkeypatch, demat_data):
    tried = []

    def fake(_f, password, **_k):
        tried.append(password)
        if password != "ABCDE1234F":
            raise IncorrectPasswordError("Incorrect PDF password!")
        return demat_data

    monkeypatch.setattr(casparser, "read_cas_pdf", fake)
    assert cas.parse_pdf_bytes(b"%PDF", "abcde1234f").issuer == "CDSL"
    assert tried == ["abcde1234f", "ABCDE1234F"]


def test_wrong_password_message(monkeypatch):
    def fake(*_a, **_k):
        raise IncorrectPasswordError("Incorrect PDF password!")

    monkeypatch.setattr(casparser, "read_cas_pdf", fake)
    with pytest.raises(cas.CasImportError, match="Wrong password"):
        cas.parse_pdf_bytes(b"%PDF", "nope")


def test_unparseable_statement(monkeypatch):
    def fake(*_a, **_k):
        raise CASParseError("Could not identify the CAS issuer.")

    monkeypatch.setattr(casparser, "read_cas_pdf", fake)
    with pytest.raises(cas.CasImportError, match="Could not read this statement"):
        cas.parse_pdf_bytes(b"%PDF", "pw")


def test_garbage_bytes_rejected():
    with pytest.raises(cas.CasImportError):
        cas.parse_pdf_bytes(b"not a pdf at all", "pw")


def test_missing_period_rejected(monkeypatch, demat_data):
    demat_data.statement_period = demat_data.statement_period.model_copy(update={"to": ""})
    monkeypatch.setattr(casparser, "read_cas_pdf", lambda *a, **k: demat_data)
    with pytest.raises(cas.CasImportError, match="statement period"):
        cas.parse_pdf_bytes(b"%PDF", "pw")


def test_summary_shapes(mf_data, demat_data):
    s = cas.summary(cas.from_mf_data(mf_data))
    assert (s["issuer"], s["funds"], s["folios"], s["transactions"]) == ("CAMS", 3, 3, 2)
    assert s["total_value"] == 8040 + 10000 + 800 and s["total_cost"] == 6000 + 9500 + 700
    d = cas.summary(cas.from_demat_data(demat_data))
    assert (d["stocks"], d["funds"], d["bonds"]) == (5, 1, 0)
    assert len(d["accounts"]) == 4 and d["accounts"][0]["broker"] == "zerodha" and d["accounts"][0]["gap"] == 0


# ---- command line ----------------------------------------------------------------------


@pytest.fixture
def cli_env(tmp_path, monkeypatch, mf_data):
    pdf = tmp_path / "cas.pdf"
    pdf.write_bytes(b"%PDF-fake")
    monkeypatch.chdir(tmp_path)  # no stray .env picked up
    monkeypatch.setenv("PORTFOLIO_DATABASE_URL", f"sqlite:///{tmp_path}/cli.db")
    get_settings.cache_clear()
    seen = {}

    def fake_parse(pdf_bytes, password):
        seen["password"] = password
        return cas.from_mf_data(mf_data)

    monkeypatch.setattr(cas, "parse_pdf_bytes", fake_parse)
    monkeypatch.setattr(cas.getpass, "getpass", lambda _prompt: " secret-pw ")
    yield pdf, tmp_path, seen
    get_settings.cache_clear()


def test_cli_dry_run(cli_env, capsys):
    pdf, tmp_path, seen = cli_env
    assert cas.main([str(pdf)]) == 0
    out = capsys.readouterr().out
    assert seen["password"] == "secret-pw" and "secret-pw" not in out
    assert "CAMS statement (DETAILED), 2026-04-01 to 2026-09-30" in out
    assert "Parag Parikh Flexi Cap Fund" in out and "****7/89" in out
    assert "Dry run: nothing saved" in out
    assert Store.from_url(f"sqlite:///{tmp_path}/cli.db").list_statements() == []


def test_cli_save_then_duplicate(cli_env, capsys):
    pdf, _, _ = cli_env
    assert cas.main([str(pdf), "--save"]) == 0
    assert "Saved as statement #1" in capsys.readouterr().out
    assert cas.main([str(pdf), "--save"]) == 0
    assert "Already imported" in capsys.readouterr().out


def test_cli_missing_file(tmp_path):
    assert cas.main([str(tmp_path / "nope.pdf")]) == 2


def test_file_type_enum_or_string():
    assert cas._enum_name(FileType.KFINTECH) == "KFINTECH"
    assert cas._enum_name("CAMS") == "CAMS"


# ---- re-import, migration, diagnose ----------------------------------------------------


def test_older_parser_version_is_replaced(tmp_path, demat_data):
    store = Store.from_url(f"sqlite:///{tmp_path}/t.db")
    st = cas.from_demat_data(demat_data)
    old_id, _ = store.save_statement(file_sha256="h", issuer="CDSL", kind="demat", cas_type=None,
                                     period_from=None, period_to=st.period_to, warnings=[], parser_version=1,
                                     stocks=st.stocks[:2])
    new_id, created = cas.save(store, st, "h")
    assert created
    [only] = store.list_statements()
    assert only["id"] == new_id and only["parser_version"] == cas.PARSER_VERSION
    from portfolio.store import stock_holdings
    assert len(store.rows_for(stock_holdings, new_id)) == len(st.stocks) == 6  # old 2-row parse replaced
    assert cas.save(store, st, "h") == (new_id, False)  # same version again: no-op


def test_migration_adds_new_columns(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE statements (id INTEGER PRIMARY KEY, file_sha256 VARCHAR(64) NOT NULL UNIQUE,
            issuer VARCHAR(16) NOT NULL, kind VARCHAR(8) NOT NULL, cas_type VARCHAR(16), period_from DATE,
            period_to DATE NOT NULL, imported_at DATETIME NOT NULL, warnings TEXT);
        CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, statement_id INTEGER NOT NULL,
            depository VARCHAR(8) NOT NULL, dp_name VARCHAR(256), broker VARCHAR(32),
            client_id_masked VARCHAR(32), isin VARCHAR(12) NOT NULL, name VARCHAR(256),
            quantity NUMERIC NOT NULL, price NUMERIC, value NUMERIC);
        INSERT INTO statements VALUES (1, 'h', 'CDSL', 'demat', NULL, NULL, '2026-09-30', '2026-10-27', NULL);
        INSERT INTO stock_holdings VALUES (1, 1, 'CDSL', 'X', 'zerodha', '****1', 'INE009A01021', 'INFY', 1, 1, 1);
    """)
    con.commit()
    con.close()
    store = Store.from_url(f"sqlite:///{db}")
    assert store.list_statements()[0]["parser_version"] == 1
    from portfolio.store import stock_holdings
    assert store.rows_for(stock_holdings, 1)[0]["asset_type"] == "equity"


def _pdf_with_text(lines):
    """A minimal real PDF whose text pdfium can extract."""
    stream = "BT /F1 9 Tf 40 800 Td 12 TL " + " ".join(f"({l}) '" for l in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for i, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n"
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    return out.encode()


def test_diagnose_lists_isins_not_imported(demat_data):
    st = cas.from_demat_data(demat_data)
    pdf = _pdf_with_text([
        "HOLDING STATEMENT AS ON 30-09-2026",
        "INE009A01021 INFOSYS LIMITED 12 -- -- -- 12 1,450.00 17,400.00",
        "INE155A01022 TATA MOTORS LIMITED 40 -- -- -- 40 980.00 39,200.00",
        "INF204KB14I2 NIPPON INDIA ETF GOLD BEES",
    ])
    missing = cas.diagnose(pdf, "anything", st)
    assert [isin for _, isin, _ in missing] == ["INE155A01022", "INF204KB14I2"]
    page, _, context = missing[0]
    assert page == 1 and "TATA MOTORS LIMITED" in context and "39,200.00" in context


# ---- names, symbols, classification, duplicate rows ---------------------------------


def test_isin_lookup_uses_bundled_database():
    info = cas.lookup_isins({"INE040A01034", "IN0020230184", "INF0R8F01042", "INE000NOTREAL"})
    assert info["INE040A01034"] == {"name": "HDFC BANK LIMITED", "symbol": "HDFCBANK", "type": "EQUITY SHARES"}
    assert info["IN0020230184"]["type"] == "SOVEREIGN GOLD BOND"
    assert info["INF0R8F01042"]["name"] == "ZERODHA GOLD ETF"
    assert "INE000NOTREAL" not in info


def test_isin_lookup_failure_is_harmless(monkeypatch):
    import casparser_isin.isin as isin_module

    class Broken:
        def __enter__(self):
            raise OSError("db missing")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(isin_module, "ISINDb", Broken)
    assert cas.lookup_isins({"INE040A01034"}) == {}


@pytest.mark.parametrize("name,asset_type,expected", [
    ("NIPPON LIFE INDIA AM LTD#NIPPON INDIA ETF GOLD BEES", "fund", "NIPPON INDIA ETF GOLD BEES"),
    ("LTD#ICICI PRUDENTIAL MF#ICICI PRUDENTIAL NIFTY ETF", "fund", "ICICI PRUDENTIAL NIFTY ETF"),
    ("GOVT OF INDIA#2.5% SGB 2023-24 SERIES IV", "bond", "2.5% SGB 2023-24 SERIES IV"),
    ("POLYCAB INDIA LIMITED # EQUITY SHARES", "equity", "POLYCAB INDIA LIMITED"),
    ("INFOSYS LIMITED - EQUITY SHARES OF RS 5/-", "equity", "INFOSYS LIMITED"),
    ("", "equity", None), (None, "fund", None),
])
def test_clean_statement_name(name, asset_type, expected):
    assert cas.clean_statement_name(name, asset_type) == expected


@pytest.mark.parametrize("isin,parsed_as,info,expected", [
    ("IN0020230184", "equity", None, "bond"),                           # SGB parsed as equity
    ("INE123X01010", "equity", {"type": "DEBENTURES"}, "bond"),
    ("INE040A01034", "equity", {"type": "EQUITY SHARES"}, "equity"),
    ("INF0R8F01042", "fund", {"type": "FUND"}, "fund"),
])
def test_classify(isin, parsed_as, info, expected):
    assert cas.classify(isin, parsed_as, info) == expected


def test_sgb_becomes_bond_with_statement_name(demat_data):
    acct = demat_data.accounts[0]
    sgb = cas_equity("IN0020230184", 3, "12000", "GOVT OF INDIA#2.5% SGB 2023-24 SERIES IV")
    demat_data.accounts[0] = acct.model_copy(update={"equities": acct.equities + [sgb],
                                                     "balance": acct.balance + sgb.value})
    st = cas.from_demat_data(demat_data)
    row = next(r for r in st.stocks if r["isin"] == "IN0020230184")
    assert (row["asset_type"], row["name"]) == ("bond", "2.5% SGB 2023-24 SERIES IV")
    zerodha = st.accounts[0]
    assert (zerodha.stocks, zerodha.funds, zerodha.bonds, zerodha.gap) == (2, 1, 1, 0)
    hdfc_bank = next(r for r in st.stocks if r["isin"] == "INE040A01034")
    assert (hdfc_bank["name"], hdfc_bank["symbol"]) == ("HDFC BANK LIMITED", "HDFCBANK")


def test_zero_value_duplicate_dropped(demat_data):
    # CDSL can list unlisted shares twice in one account: once valued, once at ₹0.
    acct = demat_data.accounts[0]
    valued = cas_equity("INE721I01024", 8, "1500", "NATIONAL STOCK EXCHANGE OF INDIA LIMITED")
    copy = cas_equity("INE721I01024", 8, "0", "NATIONAL STOCK EXCHANGE OF INDIA LIMITED")
    demat_data.accounts[0] = acct.model_copy(update={"equities": acct.equities + [valued, copy],
                                                     "balance": acct.balance + valued.value})
    st = cas.from_demat_data(demat_data)
    nse = [r for r in st.stocks if r["isin"] == "INE721I01024"]
    assert len(nse) == 1 and nse[0]["quantity"] == D(8) and nse[0]["value"] == D(12000)
    assert any("listed twice" in n for n in st.notes) and st.warnings == []


def test_other_repeats_kept_and_flagged(demat_data):
    acct = demat_data.accounts[0]
    a = cas_equity("INE721I01024", 8, "1500")
    b = cas_equity("INE721I01024", 5, "1500")
    demat_data.accounts[0] = acct.model_copy(update={"equities": acct.equities + [a, b],
                                                     "balance": acct.balance + a.value + b.value})
    st = cas.from_demat_data(demat_data)
    assert sum(r["quantity"] for r in st.stocks if r["isin"] == "INE721I01024") == D(13)
    assert any("appears 2 times" in w for w in st.warnings)



# ---- mutual fund folios on NSDL/CDSL statements ---------------------------------


def test_folios_imported_from_depository_statement(demat_data):
    st = cas.from_demat_data(demat_data)
    assert len(st.mfs) == 2 and st.warnings == []
    ppfas = next(m for m in st.mfs if m["isin"] == "INF879O01027")
    assert ppfas["amc"] == "PPFAS Mutual Fund" and ppfas["folio_masked"] == "****7/89"
    assert (ppfas["units"], ppfas["cost"], ppfas["value"]) == (D(50), D(3000), D(4000))
    assert ppfas["nav_date"] == date(2026, 9, 30)
    assert "PARAG PARIKH FLEXI CAP" in ppfas["scheme"].upper() and not ppfas["scheme"].startswith("PPFCF")
    other = next(m for m in st.mfs if m["isin"] == "INF999Z01011")
    assert (other["amc"], other["scheme"], other["cost"]) == (None, "Some Other Fund", None)
    s = cas.summary(st)
    assert (s["folio_funds"], s["folio_value"], s["folio_cost"]) == (2, 5000, 3000)


def test_folio_section_gap_warned(demat_data):
    folios = demat_data.accounts[-1]
    demat_data.accounts[-1] = folios.model_copy(update={"balance": D(5500)})
    [warning] = cas.from_demat_data(demat_data).warnings
    assert warning.startswith("Mutual fund folios: statement total ₹5,500.00") and "₹500.00 is missing" in warning


@pytest.mark.parametrize("isin,amc", [
    ("INF879O01027", "PPFAS Mutual Fund"), ("INF204KB14I2", "Nippon India Mutual Fund"),
    ("INF0R8F01042", "Zerodha Mutual Fund"), ("INF999Z01011", None), (None, None),
])
def test_amc_for(isin, amc):
    assert cas.amc_for(isin) == amc


@pytest.mark.parametrize("name,expected", [
    ("PPFCF - Parag Parikh Flexi Cap Fund", "Parag Parikh Flexi Cap Fund"),
    ("Parag Parikh Flexi Cap Fund - Direct", "Parag Parikh Flexi Cap Fund - Direct"),
    (None, None),
])
def test_clean_scheme_name(name, expected):
    assert cas.clean_scheme_name(name) == expected
