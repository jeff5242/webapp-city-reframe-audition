"""離線產生格式母版 schema（① ODT → JSON）。

用官方公開範本 ODT 產生 `auditor/templates/schema_<version>_<doc>.json`。
ODT 為都更處公告之空白範本，不含個資，故此步驟可在任何環境執行。

用法：
    python scripts/build_template_schema.py <ODT目錄> [--version 113]

<ODT目錄> 內檔名須含文件類型關鍵字（事業計畫書 / 權利變換計畫書 / 事業概要計畫書 …）。
範本下載連結見 auditor/templates/README.md。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 允許 `python scripts/xxx.py` 直接執行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from auditor.extractors.template_schema import parse_odt, save_schema  # noqa: E402

# 檔名關鍵字 → (canonical doc_type)。附件冊獨立成一類，主冊與附件冊分開檢核。
_DOC_TYPES = [
    ("事業概要計畫書附件冊", "事業概要計畫書附件冊"),
    ("事業概要計畫書", "事業概要計畫書"),
    ("事業計畫書附件冊", "事業計畫書附件冊"),
    ("事業計畫書", "事業計畫書"),
    ("權利變換計畫書附件冊", "權利變換計畫書附件冊"),
    ("權利變換計畫書", "權利變換計畫書"),
]


def _doc_type_of(filename: str) -> str | None:
    for keyword, canonical in _DOC_TYPES:
        if keyword in filename:
            return canonical
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("odt_dir", help="放置官方 ODT 範本的目錄")
    ap.add_argument("--version", default="113")
    args = ap.parse_args()

    odt_dir = Path(args.odt_dir)
    if not odt_dir.is_dir():
        print(f"目錄不存在：{odt_dir}", file=sys.stderr)
        return 1

    built = 0
    for odt in sorted(odt_dir.glob("*.odt")):
        doc_type = _doc_type_of(odt.name)
        if doc_type is None:
            print(f"跳過（無法判定文件類型）：{odt.name}", file=sys.stderr)
            continue
        try:
            schema = parse_odt(
                str(odt), doc_type=doc_type, version=args.version,
                source=f"臺北市都更處 {args.version} 年版官方範本 ODT（{odt.name}）",
            )
        except ValueError:
            # 附件冊是一疊表單範本、非章節式文件，無序標頂層節 → 不適用此 schema，跳過。
            print(f"跳過（非章節式文件，無頂層節）：{odt.name}", file=sys.stderr)
            continue
        out = save_schema(schema)
        print(f"✓ {doc_type}: {len(schema.chapters)} 章 + {len(schema.appendices)} 附錄 "
              f"→ {out.name}")
        built += 1

    print(f"\n共產生 {built} 份 schema。")
    return 0 if built else 2


if __name__ == "__main__":
    raise SystemExit(main())
