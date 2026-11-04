"""Obtém imagens fixadas e compila o backend original do fork Memos do autor."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.real_apps import prepare

if __name__ == '__main__':
    prepare()
