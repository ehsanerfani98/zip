#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_services_from_json.py
==========================
همگام‌سازی جدول services دیتابیس SQLite با فایل JSON خدمات

قواعد همگام‌سازی:
    1. خدماتی که فقط در JSON هستند  →  به دیتابیس اضافه می‌شوند
    2. خدماتی که فقط در دیتابیس هستند  →  از دیتابیس حذف می‌شوند
    3. خدمات مشترک (تطبیق با عنوان)  →  فقط مقادیر JSON به‌روزرسانی می‌شوند

فیلدهایی که از JSON وارد می‌شوند:
    price, description, noticeType, noticeText, title, benefit

نگاشت به ستون‌های دیتابیس:
    price       →  base_price   (ستون موجود)
    description →  description  (ستون موجود)
    title       →  title        (ستون موجود)
    noticeType  →  notice_type  (ستون جدید - خودکار ساخته می‌شود)
    noticeText  →  notice_text  (ستون جدید - خودکار ساخته می‌شود)
    benefit     →  benefit      (ستون جدید - خودکار ساخته می‌شود)

نکته: ستون estimated_time (زمان انجام کار) دست نمی‌خورد و همان مقدار
قبلی دیتابیس برای خدمات جدید هم استفاده می‌شود.

نحوه اجرا:
    python sync_services_from_json.py                       # فایل‌ها را خودکار پیدا می‌کند
    python sync_services_from_json.py --dry-run             # فقط پیش‌نمایش، بدون تغییر
    python sync_services_from_json.py services.json db.sqlite

امکانات:
    - بکاپ خودکار از دیتابیس قبل از تغییر (با تاریخ و ساعت)
    - تطبیق هوشمند عناوین فارسی (نیم‌فاصله، اعداد فارسی، فاصله‌های اضافی)
    - اجرای تراکنشی و امن (در صورت خطا rollback کامل)
    - گزارش کامل قبل/بعد
"""

import json
import re
import shutil
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

# ------------------------------------------------- تنظیمات نگاشت فیلدها ----
# کلید JSON  →  ستون دیتابیس
FIELD_MAP = {
    "price": "base_price",       # موجود
    "description": "description",  # موجود
    "title": "title",              # موجود
    "noticeType": "notice_type",   # جدید
    "noticeText": "notice_text",   # جدید
    "benefit": "benefit",          # جدید
}

# تعریف SQL ستون‌هایی که ممکن است در جدول نباشند
NEW_COLUMNS = {
    "notice_type": "TEXT",
    "notice_text": "TEXT",
    "benefit": "INTEGER",
}

# دسته پیش‌فرض برای خدمات جدید (عمومی / بدون دسته‌بندی مشخص)
DEFAULT_CATEGORY_HINT = "%بدون دسته%"
DEFAULT_CATEGORY_FALLBACK = "cat-amvmi-bdvn-dsthbndi-mshkhs"

DEFAULT_ESTIMATED_TIME_FALLBACK = "۳۰ الی ۶۰ دقیقه"


# ------------------------------------------------------------ توابع کمکی ---
def normalize_title(s: str) -> str:
    """نرمال‌سازی عنوان فارسی برای تطبیق مطمئن:
    یکسان‌سازی نیم‌فاصله، حروف عربی/فارسی، اعداد فارسی و فاصله‌ها."""
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    s = s.replace("ي", "ی").replace("ك", "ک")
    for fa, en in zip("۰۱۲۳۴۵۶۷۸۹", "0123456789"):
        s = s.replace(fa, en)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def find_files(argv: list[str]) -> tuple[Path, Path]:
    """پیدا کردن فایل JSON و دیتابیس از آرگومان‌ها یا پوشه فعلی."""
    given = [Path(a) for a in argv if not a.startswith("--")]
    json_path = next((p for p in given if p.suffix.lower() == ".json"), None)
    db_path = next((p for p in given if p.suffix.lower() in (".sqlite", ".db")), None)
    if json_path is None:
        candidates = sorted(Path(".").glob("*.json"))
        json_path = candidates[0] if candidates else None
    if db_path is None:
        candidates = sorted(Path(".").glob("*.sqlite")) + sorted(Path(".").glob("*.db"))
        db_path = candidates[0] if candidates else None
    if json_path is None or db_path is None:
        sys.exit("خطا: فایل JSON یا دیتابیس پیدا نشد. مسیرها را دستی وارد کنید.")
    if not json_path.exists() or not db_path.exists():
        sys.exit("خطا: یکی از فایل‌های ورودی وجود ندارد.")
    return json_path, db_path


def load_json_items(json_path: Path) -> list[dict]:
    """خواندن لیست خدمات از فایل JSON (پشتیبانی از dict دارای items یا لیست مستقیم)."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    items = data["items"] if isinstance(data, dict) and "items" in data else data
    if not isinstance(items, list):
        sys.exit("خطا: ساختار JSON قابل قبول نیست (لیست خدمات پیدا نشد).")
    return items


def ensure_columns(cur: sqlite3.Cursor) -> list[str]:
    """ساخت ستون‌های ناقص در جدول services و برگرداندن لیست ستون‌های ساخته‌شده."""
    existing = {row[1] for row in cur.execute("PRAGMA table_info(services)").fetchall()}
    created = []
    for col, decl in NEW_COLUMNS.items():
        if col not in existing:
            cur.execute(f'ALTER TABLE services ADD COLUMN "{col}" {decl}')
            created.append(col)
    return created


def make_backup(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}_backup_{stamp}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def pick_default_values(cur: sqlite3.Cursor) -> dict:
    """مقادیر پیش‌فرض برای ردیف‌های جدید، بر اساس مقادیر موجود در دیتابیس."""
    # مقدار رایج estimated_time در دیتابیس فعلی (همان مقدار قبلی)
    row = cur.execute(
        "SELECT estimated_time, COUNT(*) AS c FROM services "
        "GROUP BY estimated_time ORDER BY c DESC LIMIT 1"
    ).fetchone()
    estimated_time = row[0] if row and row[0] else DEFAULT_ESTIMATED_TIME_FALLBACK

    # دسته پیش‌فرض: «عمومی / بدون دسته‌بندی مشخص»
    row = cur.execute(
        "SELECT id FROM categories WHERE title LIKE ? LIMIT 1",
        (DEFAULT_CATEGORY_HINT,),
    ).fetchone()
    category_id = row[0] if row else DEFAULT_CATEGORY_FALLBACK

    return {"estimated_time": estimated_time, "category_id": category_id}


# ------------------------------------------------------------------ main ---
def main() -> None:
    dry_run = "--dry-run" in sys.argv
    json_path, db_path = find_files(sys.argv[1:])
    print(f"فایل JSON : {json_path.resolve()}")
    print(f"دیتابیس   : {db_path.resolve()}")
    if dry_run:
        print("حالت پیش‌نمایش (dry-run): هیچ تغییری ذخیره نخواهد شد.\n")

    items = load_json_items(json_path)
    con = sqlite3.connect(db_path)
    cur = con.cursor()
    defaults = pick_default_values(cur)

    # --- خواندن خدمات فعلی دیتابیس و تطبیق با عنوان نرمال‌شده ---
    db_rows = cur.execute(
        "SELECT id, title FROM services"
    ).fetchall()
    db_by_norm: dict[str, list[tuple[str, str]]] = {}
    for service_id, title in db_rows:
        db_by_norm.setdefault(normalize_title(title), []).append((service_id, title))

    json_by_norm: dict[str, dict] = {}
    for it in items:
        json_by_norm[normalize_title(it["title"])] = it

    matched_norms = set(db_by_norm) & set(json_by_norm)
    add_norms = set(json_by_norm) - set(db_by_norm)
    delete_norms = set(db_by_norm) - set(json_by_norm)

    to_update = [json_by_norm[n] for n in sorted(matched_norms)]
    to_add = [json_by_norm[n] for n in sorted(add_norms)]
    to_delete = [sid for n in sorted(delete_norms) for sid, _t in db_by_norm[n]]

    print(f"خدمات JSON: {len(items)} | خدمات دیتابیس: {len(db_rows)}")
    print(f"مشترک (به‌روزرسانی): {len(to_update)}")
    print(f"فقط JSON (اضافه): {len(to_add)}")
    print(f"فقط دیتابیس (حذف): {len(to_delete)}\n")

    print("--- نمونه خدمات جدید (اولین ۵ مورد) ---")
    for it in to_add[:5]:
        print(f"  + {it['title']}")
    print("--- نمونه خدمات حذفی (اولین ۵ مورد) ---")
    titles_lookup = {sid: t for sid, t in db_rows}
    for sid in to_delete[:5]:
        print(f"  - {titles_lookup.get(sid, sid)}")
    print()

    if dry_run:
        print("پیش‌نمایش کامل شد. برای اعمال واقعی، بدون --dry-run اجرا کنید.")
        con.close()
        return

    # --- بکاپ و اعمال تغییرات ---
    backup_path = make_backup(db_path)
    print(f"بکاپ ساخته شد: {backup_path.name}")

    created_cols = ensure_columns(cur)
    if created_cols:
        print(f"ستون‌های جدید ساخته شد: {', '.join(created_cols)}")

    used_ids = {sid for sid, _t in db_rows}
    try:
        cur.execute("BEGIN")

        # ۱) به‌روزرسانی خدمات مشترک (estimated_time دست نمی‌خورد)
        cur.executemany(
            """UPDATE services SET
                   title = ?, base_price = ?, description = ?,
                   notice_type = ?, notice_text = ?, benefit = ?
               WHERE id = ?""",
            [
                (
                    it["title"],
                    it.get("price"),
                    it.get("description"),
                    it.get("noticeType"),
                    it.get("noticeText"),
                    it.get("benefit"),
                    sid,
                )
                for it in to_update
                for (sid, _t) in db_by_norm[normalize_title(it["title"])]
            ],
        )

        # ۲) حذف خدمات اضافی (سفارش‌های تاریخی حفظ می‌شوند)
        cur.executemany(
            "DELETE FROM services WHERE id = ?",
            [(sid,) for sid in to_delete],
        )

        # ۳) افزودن خدمات جدید از JSON
        insert_rows = []
        for it in to_add:
            new_id = f"srv-{it['id']}"
            while new_id in used_ids:
                new_id += "-x"
            used_ids.add(new_id)
            slug = (it["title"] or "").replace(" ", "-")
            insert_rows.append(
                (
                    new_id,
                    defaults["category_id"],
                    it["title"],
                    it.get("description"),
                    it.get("price"),
                    defaults["estimated_time"],  # همان مقدار قبلی دیتابیس
                    None,          # icon
                    1,             # is_active
                    "[]",          # fields_json
                    "",            # pricing_formula
                    None,          # icon_color
                    0,             # sort_order
                    0,             # is_featured
                    0,             # featured_sort
                    slug,
                    None,          # created_at
                    it.get("noticeType"),
                    it.get("noticeText"),
                    it.get("benefit"),
                )
            )
        cur.executemany(
            """INSERT INTO services (
                   id, category_id, title, description, base_price,
                   estimated_time, icon, is_active, fields_json,
                   pricing_formula, icon_color, sort_order, is_featured,
                   featured_sort, slug, created_at,
                   notice_type, notice_text, benefit
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            insert_rows,
        )

        con.commit()
    except Exception as e:
        con.rollback()
        con.close()
        sys.exit(f"خطا هنگام به‌روزرسانی ({e})! تغییرات برگردانده شد (rollback).")

    # --- تأیید نهایی ---
    total = cur.execute("SELECT COUNT(*) FROM services").fetchone()[0]
    cols = {row[1] for row in cur.execute("PRAGMA table_info(services)").fetchall()}
    con.close()

    print("\n--- نتیجه ---")
    print(f"تعداد خدمات پس از همگام‌سازی: {total} (انتظار: {len(items)})")
    print(f"ستون‌های جدول services: {sorted(cols)}")
    if total == len(items):
        print("✅ همگام‌سازی با موفقیت انجام شد.")
    else:
        print("⚠️ تعداد نهایی با JSON یکسان نیست؛ فایل بکاپ را بررسی کنید.")


if __name__ == "__main__":
    main()
