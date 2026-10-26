#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_services_from_json.py  (نسخه جایگزینی کامل)
================================================
۱) همه خدمات قبلیِ جدول services دیتابیس حذف می‌شوند
۲) همه خدمات فایل JSON از نو اضافه می‌شوند

فیلدهایی که از JSON وارد می‌شوند (فقط همین‌ها):
    price, description, noticeType, noticeText, title, benefit

نگاشت به ستون‌های دیتابیس:
    price       →  base_price   (ستون موجود)
    description →  description  (ستون موجود)
    title       →  title        (ستون موجود)
    noticeType  →  notice_type  (ستون جدید - در صورت نبود خودکار ساخته می‌شود)
    noticeText  →  notice_text  (ستون جدید - در صورت نبود خودکار ساخته می‌شود)
    benefit     →  benefit      (ستون جدید - در صورت نبود خودکار ساخته می‌شود)

نکته: ستون estimated_time (زمان انجام کار) همان مقدار قبلی دیتابیس است
(رایج‌ترین مقدار موجود قبل از حذف، مثلا «۳۰ الی ۶۰ دقیقه»).

نحوه اجرا:
    python sync_services_from_json.py                       # فایل‌ها را خودکار پیدا می‌کند
    python sync_services_from_json.py --dry-run             # فقط پیش‌نمایش، بدون تغییر
    python sync_services_from_json.py services.json db.sqlite

امکانات:
    - بکاپ خودکار از دیتابیس قبل از تغییر (با تاریخ و ساعت)
    - اجرای تراکنشی و امن (در صورت خطا rollback کامل)
    - گزارش کامل و تأیید نهایی
"""

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ------------------------------------------------- تنظیمات نگاشت فیلدها ----
# کلید JSON  →  ستون دیتابیس
FIELD_MAP = {
    "price": "base_price",         # موجود
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
    """مقادیر پیش‌فرض بر اساس وضعیت فعلی دیتابیس (قبل از حذف)."""
    # مقدار رایج estimated_time در دیتابیس فعلی = همان مقدار قبلی
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

    old_count = cur.execute("SELECT COUNT(*) FROM services").fetchone()[0]
    orders_ref = cur.execute(
        "SELECT COUNT(*) FROM orders WHERE service_id IS NOT NULL"
    ).fetchone()[0]

    print(f"خدمات فعلی دیتابیس: {old_count}  ← همه حذف می‌شوند")
    print(f"خدمات JSON: {len(items)}  ← همه اضافه می‌شوند")
    print(f"زمان انجام کار خدمات جدید (مقدار قبلی دیتابیس): {defaults['estimated_time']!r}")
    print(f"دسته‌بندی خدمات جدید: {defaults['category_id']}")
    print(f"سفارش‌های تاریخی که به خدمات اشاره می‌کنند (حفظ می‌شوند): {orders_ref}\n")

    print("--- نمونه خدمات JSON (اولین ۵ مورد) ---")
    for it in items[:5]:
        print(f"  + {it['title']}  (price={it.get('price')}, benefit={it.get('benefit')})")
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

    try:
        cur.execute("BEGIN")

        # ۱) حذف همه خدمات قبلی
        cur.execute("DELETE FROM services")

        # ۲) افزودن همه خدمات JSON
        insert_rows = []
        for i, it in enumerate(items):
            slug = (it["title"] or "").replace(" ", "-")
            insert_rows.append(
                (
                    f"srv-{it['id']}",                      # id
                    defaults["category_id"],                 # category_id
                    it["title"],                             # title
                    it.get("description"),                   # description
                    it.get("price"),                         # base_price
                    defaults["estimated_time"],              # estimated_time (مقدار قبلی دیتابیس)
                    None,                                    # icon
                    1,                                       # is_active
                    "[]",                                    # fields_json
                    "",                                      # pricing_formula
                    None,                                    # icon_color
                    i,                                       # sort_order (ترتیب JSON)
                    0,                                       # is_featured
                    0,                                       # featured_sort
                    slug,                                    # slug
                    None,                                    # created_at
                    it.get("noticeType"),                    # notice_type
                    it.get("noticeText"),                    # notice_text
                    it.get("benefit"),                       # benefit
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
    distinct_time = [
        r[0] for r in cur.execute("SELECT DISTINCT estimated_time FROM services").fetchall()
    ]
    cols = {row[1] for row in cur.execute("PRAGMA table_info(services)").fetchall()}
    con.close()

    print("\n--- نتیجه ---")
    print(f"خدمات قبلی حذف‌شده: {old_count}")
    print(f"خدمات جدید اضافه‌شده: {total} (انتظار: {len(items)})")
    print(f"مقادیر estimated_time: {distinct_time}")
    print(f"ستون‌های جدول services: {sorted(cols)}")
    if total == len(items):
        print("✅ جایگزینی کامل خدمات با موفقیت انجام شد.")
    else:
        print("⚠️ تعداد نهایی با JSON یکسان نیست؛ فایل بکاپ را بررسی کنید.")


if __name__ == "__main__":
    main()
