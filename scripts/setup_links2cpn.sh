#!/usr/bin/env bash
# Prepara uma referência isolada. Argumento: caminho para Python 3.9.
set -euo pipefail
if [ "$#" -ne 1 ]; then
    printf 'Uso: bash scripts/setup_links2cpn.sh /caminho/para/python3.9\n' >&2
    exit 2
fi
cpn_python39="$1"
"$cpn_python39" -c 'import sys; assert sys.version_info[:2] == (3, 9), "Use Python 3.9"'
cpn_project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cpn_reference_root="$cpn_project_root/.comparison/Links2CPN"
cpn_reference_env="$cpn_project_root/.comparison/venv-reference"
cpn_reference_commit=d6092c3d85adaf96a0e83a90a3bd2d287cd51325
mkdir -p "$cpn_project_root/.comparison"
if [ ! -d "$cpn_reference_root" ]; then
    git clone https://github.com/ailton07/openapi-links-to-CPNs.git "$cpn_reference_root"
    git -C "$cpn_reference_root" checkout --detach "$cpn_reference_commit"
fi
if [ "$(git -C "$cpn_reference_root" rev-parse HEAD)" != "$cpn_reference_commit" ]; then
    printf 'Checkout existente não corresponde ao commit esperado. Não foi alterado.\n' >&2
    exit 2
fi
git -C "$cpn_reference_root" diff --quiet HEAD --
"$cpn_python39" -m venv "$cpn_reference_env"
"$cpn_reference_env/bin/python" -m pip install 'setuptools==65.5.1' 'wheel==0.38.4'
"$cpn_reference_env/bin/python" -m pip install -r "$cpn_reference_root/requirements.txt"
printf '\nReferência preparada. Execute na raiz do projeto:\n'
printf '.venv/bin/python scripts/compare_links2cpn.py --checkout .comparison/Links2CPN --reference-python .comparison/venv-reference/bin/python\n'
printf '.venv/bin/python scripts/compare_links2cpn.py --checkout .comparison/Links2CPN --reference-python .comparison/venv-reference/bin/python --ours-profile links2cpn --output docs/comparacao_links2cpn_compat\n'
