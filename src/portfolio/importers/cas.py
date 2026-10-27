"""Parse Consolidated Account Statement PDFs.

Two kinds are supported:
- CAMS / KFintech CAS: mutual funds, with units, NAV, purchase cost and
  (in the detailed version) transactions.
- NSDL / CDSL CAS: shares in every demat account, valued at the statement
  date. It has no purchase cost.

PDFs are parsed in memory and never saved. Command-line use (dry run unless
--save):

    PYTHONPATH=src .venv/bin/python -m portfolio.importers.cas ~/cas/statement.pdf [--save]
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import io
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# Bump when parsing changes what gets imported: re-uploading a file imported
# by an older version then replaces it instead of being ignored as a duplicate.
PARSER_VERSION = 5

# Matched against the depository participant (DP) name printed on NSDL/CDSL statements.
BROKER_PATTERNS: list[tuple[str, tuple[str, ...]]] = [
    ("zerodha", ("ZERODHA",)),
    ("upstox", ("UPSTOX", "RKSV")),
    ("hdfc", ("HDFC",)),
    ("sharekhan", ("SHAREKHAN",)),
    ("groww", ("GROWW", "NEXTBILLION")),
    ("angelone", ("ANGEL",)),
    ("axis", ("AXIS",)),
]

# A statement total may differ from the sum of its rows by rounding only.
TOLERANCE = Decimal("1")

DATE_FORMATS = ("%d-%b-%Y", "%d/%m/%Y", "%d-%m-%Y", "%d/%b/%Y", "%Y-%m-%d")

ISSUER_KIND = {"CAMS": "mf", "KFINTECH": "mf", "NSDL": "demat", "CDSL": "demat"}
ISSUER_LABEL = {"CAMS": "CAMS", "KFINTECH": "KFintech", "NSDL": "NSDL", "CDSL": "CDSL"}


class CasImportError(RuntimeError):
    """A user-facing problem with the uploaded statement.

    `code` is a short fixed label. It is what the audit log records, because
    the message can quote text from the statement.
    """

    def __init__(self, message: str, code: str = "import_error") -> None:
        super().__init__(message)
        self.code = code


def map_broker(dp_name: str | None) -> str | None:
    upper = (dp_name or "").upper()
    for broker, needles in BROKER_PATTERNS:
        if any(n in upper for n in needles):
            return broker
    return None


LEGAL_SUFFIXES = {"LIMITED", "LTD", "PRIVATE", "PVT", "LLP", "INC"}
ACRONYMS = {"ICICI", "IIFL", "IDBI", "IDFC", "SBI", "UTI", "LIC"}


def broker_label(dp_name: str | None) -> str:
    """A readable broker name from the DP name printed on the statement.

    'ZERODHA BROKING LIMITED' -> 'Zerodha Broking'. Nothing is looked up, so
    any broker works and nothing needs maintaining: HDFC Bank and HDFC
    Securities stay different because the statement says so.
    """
    words = re.sub(r"[.,]", " ", dp_name or "").split()
    while words and words[-1].upper() in LEGAL_SUFFIXES:
        words.pop()
    if not words:
        return (dp_name or "").strip() or "Unknown"
    # Words without a vowel (HDFC, DP, &) and a few known names stay in capitals.
    return " ".join(w.upper() if w.upper() in ACRONYMS or not re.search("[AEIOUY]", w.upper()) else w.capitalize()
                    for w in words)


def mask_id(value: str | None) -> str:
    """Keep only the last 4 characters of a folio or client number."""
    value = (value or "").strip()
    return f"****{value[-4:]}" if len(value) > 4 else "****"


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def inr(value: Decimal) -> str:
    """₹ with Indian digit grouping: 12,34,567.89."""
    sign = "-" if value < 0 else ""
    whole, frac = f"{abs(value):.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return f"{sign}₹{','.join(groups + [tail])}.{frac}"


def lookup_isins(isins: set[str]) -> dict[str, dict[str, Any]]:
    """Clean names, NSE symbols and instrument types for ISINs.

    Uses the ISIN database bundled with casparser (a local SQLite file, no
    network). Enrichment is cosmetic, so any failure just returns less.
    """
    out: dict[str, dict[str, Any]] = {}
    try:
        from casparser_isin.isin import ISINDb
        from casparser_isin.mf_isin import MFISINDb

        with ISINDb() as db:
            for isin, d in db.batch_isin_lookup(isins).items():
                out[isin] = {"name": d.issuer or None, "symbol": d.symbol, "type": (d.type or "").upper()}
        with MFISINDb() as db:
            for isin in sorted(i for i in isins if i.startswith("INF") and not (out.get(i) or {}).get("name")):
                rows = db.direct_isin_lookup(isin)
                if rows:
                    out[isin] = {"name": " ".join(rows[0]["name"].split()), "symbol": None, "type": "FUND"}
    except Exception:  # noqa: BLE001
        pass
    return out


# Fund house from the ISIN's issuer code (characters 4-7 of an INF ISIN).
AMC_BY_ISSUER = {
    "879O": "PPFAS Mutual Fund", "204K": "Nippon India Mutual Fund", "732E": "Nippon India Mutual Fund",
    "109K": "ICICI Prudential Mutual Fund", "200K": "SBI Mutual Fund", "769K": "Mirae Asset Mutual Fund",
    "247L": "Motilal Oswal Mutual Fund", "846K": "Axis Mutual Fund", "179K": "HDFC Mutual Fund",
    "174K": "Kotak Mahindra Mutual Fund", "209K": "Aditya Birla Sun Life Mutual Fund",
    "277K": "Tata Mutual Fund", "0R8F": "Zerodha Mutual Fund", "789F": "UTI Mutual Fund",
    "767K": "LIC Mutual Fund",
}


def amc_for(isin: str | None) -> str | None:
    return AMC_BY_ISSUER.get((isin or "")[3:7])


def clean_scheme_name(name: str | None) -> str | None:
    """CDSL folio rows prefix the scheme with its code: 'PPFCF - Parag Parikh Flexi Cap Fund'."""
    if not name:
        return None
    return re.sub(r"^\s*[A-Z0-9]{2,12}\s+-\s+", "", " ".join(name.split())) or None


def clean_statement_name(name: str | None, asset_type: str) -> str | None:
    """CDSL names look like 'ISSUER#SECURITY'. Funds and bonds: keep the security part
    ('NIPPON LIFE INDIA AM LTD#NIPPON INDIA ETF GOLD' -> 'NIPPON INDIA ETF GOLD').
    Equities: keep the issuer ('POLYCAB INDIA LIMITED # EQUITY SHARES' -> 'POLYCAB INDIA LIMITED')."""
    parts = [p.strip() for p in (name or "").split("#") if p.strip()]
    if not parts:
        return None
    if asset_type in ("fund", "bond") and len(parts) > 1:
        return parts[-1]
    return parts[0].split(" - ")[0].strip()


def classify(isin: str, parsed_as: str, info: dict[str, Any] | None) -> str:
    """Government securities (ISINs starting IN0, e.g. Sovereign Gold Bonds) are bonds,
    whichever list the parser put them in."""
    kind = (info or {}).get("type", "")
    if isin.startswith("IN0") or any(w in kind for w in ("BOND", "DEBENTURE", "GOVERNMENT")):
        return "bond"
    return parsed_as


def _enum_name(value: Any) -> str | None:
    if value is None:
        return None
    return getattr(value, "name", None) or str(value)


@dataclass
class DematAccount:
    depository: str
    dp_name: str
    broker: str | None
    client_id_masked: str
    statement_total: Decimal  # account value printed on the statement
    imported_value: Decimal   # sum of the rows we read: stocks + funds/ETFs + bonds
    stocks: int
    funds: int
    bonds: int

    @property
    def gap(self) -> Decimal:
        return self.statement_total - self.imported_value


@dataclass
class ParsedStatement:
    issuer: str
    kind: str
    cas_type: str | None
    period_from: date | None
    period_to: date | None
    stocks: list[dict[str, Any]] = field(default_factory=list)
    mfs: list[dict[str, Any]] = field(default_factory=list)
    transactions: list[dict[str, Any]] = field(default_factory=list)
    accounts: list[DematAccount] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def from_mf_data(data: Any) -> ParsedStatement:
    """casparser CASData (CAMS/KFintech) -> our records. Pure; no file access."""
    issuer = _enum_name(data.file_type)
    st = ParsedStatement(
        issuer=issuer, kind="mf", cas_type=_enum_name(data.cas_type),
        period_from=parse_date(data.statement_period.from_), period_to=parse_date(data.statement_period.to),
        warnings=list(data.parse_warnings or []),
    )
    for folio in data.folios:
        folio_masked = mask_id(folio.folio)
        for s in folio.schemes:
            for t in s.transactions:
                st.transactions.append({
                    "folio_masked": folio_masked, "isin": s.isin, "scheme": s.scheme,
                    "date": parse_date(t.date), "description": (t.description or "")[:512],
                    "type": _enum_name(t.type), "amount": t.amount, "units": t.units,
                    "nav": t.nav, "balance": t.balance,
                })
            if s.transactions and s.close != s.close_calculated:
                st.warnings.append(
                    f"{s.scheme} ({folio_masked}): closing units {s.close} differ from the sum of "
                    f"transactions {s.close_calculated}"
                )
            if not s.close:
                continue  # fully redeemed scheme: transactions kept, no holding
            st.mfs.append({
                "amc": folio.amc, "folio_masked": folio_masked, "scheme": s.scheme, "isin": s.isin,
                "amfi": s.amfi, "rta": s.rta, "scheme_type": s.type, "units": s.close,
                "nav": s.valuation.nav, "nav_date": parse_date(s.valuation.date),
                "cost": s.valuation.cost, "value": s.valuation.value,
                "open_units": s.open, "history_from": st.period_from,
            })
    if st.mfs and all(m["cost"] is None for m in st.mfs):
        st.notes.append("This statement has no purchase cost, so gains can't be shown. "
                        "Request the detailed CAS from CAMS/KFintech to get cost.")
    return st


def from_demat_data(data: Any) -> ParsedStatement:
    """casparser NSDLCASData (NSDL/CDSL) -> our records. Pure; no file access.

    Imports stocks, funds (CDSL files ETFs and demat mutual fund units under
    INF ISINs) and bonds, then checks each account's rows add up to the
    account total printed on the statement.
    """
    st = ParsedStatement(
        issuer=_enum_name(data.file_type), kind="demat", cas_type=None,
        period_from=parse_date(data.statement_period.from_), period_to=parse_date(data.statement_period.to),
        warnings=list(getattr(data, "parse_warnings", []) or []),
    )
    for acct in data.accounts:
        kind = (acct.type or "").upper()
        if "DEMAT" not in kind:
            # "Mutual Fund Folios": units held directly with the fund house (not in demat).
            _add_folio_section(st, acct)
            continue
        depository = kind.split()[0]
        broker = map_broker(acct.name)
        masked = mask_id(acct.client_id)

        candidates = (
            [("equity", e.isin, e.name, e.num_shares, e.price, e.value) for e in acct.equities]
            + [("fund", m.isin, m.name, m.balance, m.nav, m.value) for m in acct.mutual_funds]
            + [("bond", b.isin, b.name, b.num_bonds,
                b.market_price or (b.value / b.num_bonds if b.num_bonds else None), b.value) for b in acct.bonds]
        )
        rows = [
            {
                "depository": depository, "dp_name": acct.name, "broker": broker, "client_id_masked": masked,
                "asset_type": asset_type, "isin": isin.strip().upper(), "name": name, "symbol": None,
                "quantity": qty or Decimal(0), "price": price, "value": value or Decimal(0),
            }
            # Keep rows with a value even if the balance is "--" (e.g. rights entitlements).
            for asset_type, isin, name, qty, price, value in candidates if qty or value
        ]
        rows = _drop_duplicate_rows(rows, f"{acct.name} {masked}", st)
        st.stocks.extend(rows)

        account = DematAccount(
            depository=depository, dp_name=acct.name, broker=broker, client_id_masked=masked,
            statement_total=acct.balance or Decimal(0),
            imported_value=sum((r["value"] for r in rows), Decimal(0)),
            stocks=sum(1 for r in rows if r["asset_type"] == "equity"),
            funds=sum(1 for r in rows if r["asset_type"] == "fund"),
            bonds=sum(1 for r in rows if r["asset_type"] == "bond"),
        )
        st.accounts.append(account)
        if abs(account.gap) > TOLERANCE:
            st.warnings.append(
                f"{acct.name} {masked}: statement total {inr(account.statement_total)}, rows read add up to "
                f"{inr(account.imported_value)}. {inr(account.gap)} is missing. "
                "Run the command-line importer with --diagnose to list the rows it couldn't read."
            )

    info = lookup_isins({r["isin"] for r in st.stocks})
    for r in st.stocks:
        r["asset_type"] = classify(r["isin"], r["asset_type"], info.get(r["isin"]))
        found = info.get(r["isin"]) or {}
        # Bonds keep the statement's wording ("2.5% SGB 2023-24 SERIES IV"), which is clearer than the registry's.
        r["name"] = (found.get("name") if r["asset_type"] != "bond" else None) \
            or clean_statement_name(r["name"], r["asset_type"]) or r["name"]
        r["symbol"] = found.get("symbol")
    for a in st.accounts:
        own = [r for r in st.stocks if r["dp_name"] == a.dp_name and r["client_id_masked"] == a.client_id_masked]
        a.stocks, a.funds, a.bonds = (sum(1 for r in own if r["asset_type"] == t) for t in ("equity", "fund", "bond"))

    fund_info = lookup_isins({m["isin"] for m in st.mfs if m["isin"]})
    for m in st.mfs:
        m["scheme"] = (fund_info.get(m["isin"]) or {}).get("name") or m["scheme"]
    if st.mfs:
        costed = [m for m in st.mfs if m["cost"] is not None]
        st.notes.append(
            f"Imported {len(st.mfs)} mutual fund holding(s) in {len({m['folio_masked'] for m in st.mfs})} folio(s) "
            "from this statement" + ("" if costed else ", without purchase cost") + ". If you also upload a "
            "CAMS/KFintech statement, the newer one is used for mutual funds, never both."
        )
    return st


WRONG_PASSWORD = ("Wrong password. NSDL/CDSL statements use your PAN in capitals. CAMS/KFintech "
                  "statements use the password you chose when requesting the statement.")


def _password_candidates(password: str) -> list[str]:
    """The password as typed, then in capitals (NSDL/CDSL use the PAN in capitals)."""
    return [password] + ([password.upper()] if password.upper() != password else [])


def _add_folio_section(st: ParsedStatement, acct: Any) -> None:
    rows = [
        {
            "amc": amc_for(m.isin), "folio_masked": mask_id(m.folio),
            "scheme": clean_scheme_name(m.name) or m.isin, "isin": (m.isin or "").strip().upper() or None,
            "amfi": m.amfi, "rta": None, "scheme_type": m.type, "units": m.balance, "nav": m.nav,
            "nav_date": st.period_to, "cost": m.total_cost, "value": m.value,
            "open_units": None, "history_from": None,
        }
        for m in acct.mutual_funds if m.balance or m.value
    ]
    st.mfs.extend(rows)
    read = sum((r["value"] or Decimal(0) for r in rows), Decimal(0))
    total = acct.balance or Decimal(0)
    if abs(total - read) > TOLERANCE:
        st.warnings.append(
            f"Mutual fund folios: statement total {inr(total)}, rows read add up to {inr(read)}. "
            f"{inr(total - read)} is missing. Run the command-line importer with --diagnose to list them."
        )


def _drop_duplicate_rows(rows: list[dict[str, Any]], label: str, st: ParsedStatement) -> list[dict[str, Any]]:
    """CDSL sometimes prints an ISIN twice in one account: once valued, once at
    ₹0 with the same quantity (seen with unlisted shares). Keeping both would
    double the quantity, so the ₹0 copy is dropped. Other repeats are kept and
    flagged, because they may be genuinely separate balances."""
    by_isin: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_isin.setdefault(r["isin"], []).append(r)
    keep: list[dict[str, Any]] = []
    for isin, group in by_isin.items():
        if len(group) == 1:
            keep.extend(group)
            continue
        valued = [r for r in group if r["value"]]
        zero = [r for r in group if not r["value"]]
        redundant = [z for z in zero if any(v["quantity"] == z["quantity"] for v in valued)]
        kept = [r for r in group if not any(r is z for z in redundant)]
        keep.extend(kept)
        if redundant:
            st.notes.append(f"{label}: {isin} was listed twice with the same quantity; the ₹0 copy was ignored.")
        if len(kept) > 1:
            st.warnings.append(f"{label}: {isin} appears {len(kept)} times; quantities were added together. "
                               "Check this holding against the PDF.")
    return keep


def parse_pdf_bytes(pdf: bytes, password: str) -> ParsedStatement:
    import casparser
    from casparser.exceptions import IncorrectPasswordError, ParserException

    candidates = _password_candidates(password)
    for i, candidate in enumerate(candidates):
        try:
            data = casparser.read_cas_pdf(io.BytesIO(pdf), candidate, output="dict")
            break
        except IncorrectPasswordError as exc:
            if i == len(candidates) - 1:
                raise CasImportError(WRONG_PASSWORD, code="wrong_password") from exc
        except ParserException as exc:
            raise CasImportError(f"Could not read this statement: {exc}", code="unreadable") from exc
        except Exception as exc:  # pdfium raises its own errors for damaged or non-PDF files
            raise CasImportError(f"Could not open this file as a PDF ({type(exc).__name__}).",
                                 code="not_pdf") from exc

    issuer = _enum_name(data.file_type)
    kind = ISSUER_KIND.get(issuer)
    if kind == "mf":
        st = from_mf_data(data)
    elif kind == "demat":
        st = from_demat_data(data)
    else:
        raise CasImportError(f"Unsupported statement issuer: {issuer}", code="unsupported_issuer")
    if st.period_to is None:
        raise CasImportError("Could not read the statement period from this PDF.", code="no_period")
    return st


def sha256(pdf: bytes) -> str:
    return hashlib.sha256(pdf).hexdigest()


def summary(st: ParsedStatement) -> dict[str, Any]:
    """What was read, for the upload response and the command-line report."""
    num = lambda v: float(v) if v is not None else None  # noqa: E731
    out: dict[str, Any] = {
        "issuer": ISSUER_LABEL.get(st.issuer, st.issuer),
        "kind": st.kind,
        "cas_type": st.cas_type,
        "period_from": st.period_from.isoformat() if st.period_from else None,
        "period_to": st.period_to.isoformat() if st.period_to else None,
        "warnings": st.warnings,
        "notes": st.notes,
    }
    if st.kind == "mf":
        costed = [m for m in st.mfs if m["cost"] is not None]
        out.update(
            funds=len(st.mfs),
            folios=len({m["folio_masked"] for m in st.mfs}),
            total_value=num(sum((m["value"] or 0 for m in st.mfs), Decimal(0))),
            total_cost=num(sum((m["cost"] for m in costed), Decimal(0))) if costed else None,
            transactions=len(st.transactions),
        )
    else:
        count = lambda t: sum(1 for r in st.stocks if r["asset_type"] == t)  # noqa: E731
        out.update(
            stocks=count("equity"), funds=count("fund"), bonds=count("bond"),
            total_value=num(sum((r["value"] for r in st.stocks), Decimal(0))),
            folio_funds=len(st.mfs),
            folio_value=num(sum((m["value"] or Decimal(0) for m in st.mfs), Decimal(0))),
            folio_cost=num(sum((m["cost"] for m in st.mfs if m["cost"] is not None), Decimal(0)))
            if any(m["cost"] is not None for m in st.mfs) else None,
            accounts=[{
                "depository": a.depository, "broker": a.broker, "label": broker_label(a.dp_name), "dp_name": a.dp_name,
                "client": a.client_id_masked, "stocks": a.stocks, "funds": a.funds, "bonds": a.bonds,
                "imported_value": num(a.imported_value), "statement_total": num(a.statement_total),
                "gap": num(a.gap),
            } for a in st.accounts],
        )
    return out


ISIN_IN_TEXT = re.compile(r"\b(IN[EF9][0-9A-Z]{8}\d)\b")


def diagnose(pdf: bytes, password: str, st: ParsedStatement) -> list[tuple[int, str, str]]:
    """ISINs printed in the PDF that weren't imported, as (page, isin, surrounding text).

    Uses the PDF's own text, independent of casparser, to find rows the
    parser skipped. Securities sold during the period appear in the
    transaction list only, so they show up here too.
    """
    import pypdfium2 as pdfium

    imported = {r["isin"] for r in st.stocks} | {m["isin"] for m in st.mfs if m["isin"]}
    doc = None
    for candidate in _password_candidates(password):
        try:
            doc = pdfium.PdfDocument(io.BytesIO(pdf), password=candidate)
            break
        except pdfium.PdfiumError:
            continue
    if doc is None:
        raise CasImportError(WRONG_PASSWORD, code="wrong_password")

    found: dict[str, tuple[int, str]] = {}
    try:
        for page_no in range(len(doc)):
            page = doc[page_no]
            textpage = page.get_textpage()
            lines = [" ".join(line.split()) for line in textpage.get_text_range().splitlines()]
            textpage.close()
            page.close()
            for i, line in enumerate(lines):
                for isin in ISIN_IN_TEXT.findall(line):
                    if isin not in imported and isin not in found:
                        context = " | ".join(x for x in lines[i:i + 2] if x)  # names often wrap to the next line
                        found[isin] = (page_no + 1, context[:200])
    finally:
        doc.close()
    return [(page, isin, context) for isin, (page, context) in found.items()]


# ---- command line --------------------------------------------------------------


def _money(value: Any) -> str:
    return "-" if value is None else inr(Decimal(str(value)))


def _table(headers: list[str], rows: list[list[str]], right: set[int]) -> str:
    widths = [max([len(h)] + [len(r[i]) for r in rows]) for i, h in enumerate(headers)]
    def fmt(cells: list[str]) -> str:
        return "  ".join(c.rjust(w) if i in right else c.ljust(w) for i, (c, w) in enumerate(zip(cells, widths)))
    return "\n".join([fmt(headers), fmt(["-" * w for w in widths]), *(fmt(r) for r in rows)])


def print_report(st: ParsedStatement, out=None) -> None:
    p = lambda *a: print(*a, file=out or sys.stdout)  # noqa: E731
    s = summary(st)
    p(f"\n{s['issuer']} statement ({s['cas_type'] or s['kind']}), {s['period_from'] or '?'} to {s['period_to']}")
    if st.mfs:
        p("\n" + _table(
            ["AMC", "Scheme", "Folio", "Units", "NAV", "NAV date", "Cost", "Value"],
            [[(m["amc"] or "")[:28], m["scheme"][:48], m["folio_masked"], f"{m['units']:f}",
              _money(m["nav"]), str(m["nav_date"] or "-"), _money(m["cost"]), _money(m["value"])]
             for m in sorted(st.mfs, key=lambda m: m["value"] or 0, reverse=True)],
            right={3, 4, 6, 7},
        ))
    if st.kind == "mf":
        p(f"\n{s['funds']} funds in {s['folios']} folios. Value {_money(s['total_value'])}, "
          f"cost {_money(s['total_cost'])}. {s['transactions']} transactions.")
    else:
        p("\n" + _table(
            ["Depository", "Broker", "DP name", "Client", "Stocks", "Funds/ETFs", "Bonds",
             "Read", "Statement total", "Missing"],
            [[a["depository"], a["label"], a["dp_name"][:36], a["client"], str(a["stocks"]),
              str(a["funds"]), str(a["bonds"]), _money(a["imported_value"]), _money(a["statement_total"]),
              _money(a["gap"]) if abs(a["gap"]) > 1 else "-"] for a in s["accounts"]],
            right={4, 5, 6, 7, 8, 9},
        ))
        p("\n" + _table(
            ["Broker", "Type", "ISIN", "Symbol", "Name", "Quantity", "Price", "Value"],
            [[broker_label(h["dp_name"]), h["asset_type"], h["isin"], h["symbol"] or "-", (h["name"] or "")[:40],
              f"{h['quantity']:f}",
              _money(h["price"]), _money(h["value"])]
             for h in sorted(st.stocks, key=lambda h: h["value"], reverse=True)],
            right={5, 6, 7},
        ))
        p(f"\n{s['stocks']} stocks, {s['funds']} funds/ETFs, {s['bonds']} bonds. Value {_money(s['total_value'])}.")
        if st.mfs:
            p(f"Mutual fund folios: {s['folio_funds']} holdings. Value {_money(s['folio_value'])}, "
              f"invested {_money(s['folio_cost'])}.")
    for w in st.warnings:
        p(f"Warning: {w}")
    for n in st.notes:
        p(f"Note: {n}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a CAS PDF (dry run unless --save).")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--save", action="store_true", help="store the holdings in the database")
    parser.add_argument("--diagnose", action="store_true",
                        help="list ISINs printed in the PDF that weren't imported")
    args = parser.parse_args(argv)

    if not args.pdf.is_file():
        print(f"File not found: {args.pdf}", file=sys.stderr)
        return 2
    pdf = args.pdf.read_bytes()
    password = getpass.getpass("PDF password: ").strip()
    try:
        st = parse_pdf_bytes(pdf, password)
    except CasImportError as exc:
        print(exc, file=sys.stderr)
        return 1
    print_report(st)

    if args.diagnose:
        missing = diagnose(pdf, password, st)
        if not missing:
            print("\nDiagnose: every ISIN printed in the PDF was imported.")
        else:
            print(f"\nDiagnose: {len(missing)} ISIN(s) in the PDF were not imported. Some may be securities "
                  "sold during the period, which appear only in the transaction list.\n")
            reason = lambda c: ("mutual fund scheme listed without a holding (e.g. fully redeemed)"  # noqa: E731
                                if "RTA :" in c else "not in holdings: check whether it was sold this period")
            print(_table(["Page", "ISIN", "Likely reason", "Text in the PDF"],
                         [[str(p), i, reason(c), c] for p, i, c in missing], right={0}))

    if not args.save:
        print("\nDry run: nothing saved. Re-run with --save once the numbers match your PDF.")
        return 0
    from portfolio.config import get_settings
    from portfolio.store import Store

    store = Store.from_url(get_settings().database_url)
    statement_id, created = save(store, st, sha256(pdf))
    print(f"\nSaved as statement #{statement_id}." if created else f"\nAlready imported (statement #{statement_id}).")
    return 0


def save(store: Any, st: ParsedStatement, file_hash: str) -> tuple[int, bool]:
    statement_id, created = store.save_statement(
        file_sha256=file_hash, issuer=st.issuer, kind=st.kind, cas_type=st.cas_type,
        period_from=st.period_from, period_to=st.period_to, warnings=st.warnings,
        parser_version=PARSER_VERSION, stocks=st.stocks, mfs=st.mfs, transactions=st.transactions,
    )
    store.audit("statement_imported" if created else "statement_duplicate",
                f"#{statement_id} {st.issuer} to {st.period_to}")
    return statement_id, created


if __name__ == "__main__":
    raise SystemExit(main())
