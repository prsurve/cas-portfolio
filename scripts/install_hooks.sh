#!/bin/sh
# Run the privacy check before every commit and push in this clone.
set -e
cd "$(git rev-parse --show-toplevel)"
printf '#!/bin/sh\nexec python3 scripts/privacy_check.py --staged\n' > .git/hooks/pre-commit
printf '#!/bin/sh\nexec python3 scripts/privacy_check.py\n' > .git/hooks/pre-push
chmod +x .git/hooks/pre-commit .git/hooks/pre-push
echo "Installed pre-commit and pre-push privacy hooks."
