#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_service_duration.py
==========================
تغییر زمان تخمینیِ همه خدمات از «۲ الی ۲۴ ساعت» به «۳۰ الی ۶۰ دقیقه»

این اسکریپت به دیتابیس SQLite متصل می‌شود و مقدار ستون estimated_time
در جدول services را برای همه خدمات به‌روزرسانی می‌کند.

نحوه اجرا:
    python update_service_duration.py                          # دیتابیس را در پوشه فعلی پیدا می‌کند
    python update_service_duration.py "caffenet (2).sqlite"    # با مسیر مشخص
    python update_service_duration.py --dry-run                # فقط پیش‌نمایش، بدون تغییر

امکانات:
    - گرفتن بکاپ خودکار از دیتابیس قبل از هر تغییری (با پسوند .bak)
    - تشخیص هوشمند هر الگویی از «X الی Y ساعت» (با اعداد فارسی یا انگلیسی)
    - اجرای تراکنشی و امن (در صورت خطا، هیچ تغییری ذخیره نمی‌شود)
    - گزارش قبل/بعد و تأیید نهایی
"""

import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------- تنظیمات ---
NEW_VALUE = "۳۰ الی ۶۰ دقیقه"

# هر الگویی مثل «۲ الی ۲۴ ساعت» / «2 الی 24 ساعت» را پیدا می‌کند
# (اعداد فارسی ۰-۹ یا انگلیسی 0-9) و آن را با مقدار جدید جایگزین می‌کند
HOURS_PATTERN = re.compile(r"\s*[۰-۹0-9]+\s*الی\s*[۰-۹0-9]+\s*ساعت\s*")


def find_database(argv: list[str]) -> Path:
    """پیدا کردن فایل دیتابیس از آرگومان خط فرمان یا پوشه فعلی."""
    # اگر مسیر داده شده، همان را استفاده کن
    candidates = [Path(a) for a in argv if not a.startswith("--")]
    # در غیر این صورت اولین فایل sqlite موجود در پوشه فعلی
    if not candidates:
        candidates = sorted(Path(".").glob("*.sqlite")) + sorted(Path(".").glob("*.db"))
    if not candidates:
        sys.exit("خطا: هیچ فایل دیتابیس (.sqlite / .db) پیدا نشد. مسیر را دستی وارد کنید.")
    db_path = candidates[0]
    if not db_path.exists():
        sys.exit(f"خطا: فایل «{db_path}» وجود ندارد.")
    return db_path


def make_backup(db_path: Path) -> Path:
    """بکاپ با تاریخ و ساعت قبل از تغییر."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_name(f"{db_path.stem}_backup_{stamp}{db_path.suffix}")
    shutil.copy2(db_path, backup_path)
    return backup_path


def new_value_for(raw: str | None) -> str | None:
    """مقدار جدید را برای یک ردیف محاسبه می‌کند. None یعنی نیازی به تغییر نیست."""
    if raw is None:
        return None
    if HOURS_PATTERN.search(raw):
        return HOURS_PATTERN.sub(NEW_VALUE, raw).strip()
    return None


def main() -> None:
    dry_run = "--dry-run" in sys.argv
    db_path = find_database(sys.argv[1:])

    print(f"دیتابیس: {db_path.resolve()}")
    if dry_run:
        print("حالت پیش‌نمایش (dry-run): هیچ تغییری ذخیره نخواهد شد.\n")

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # خواندن همه خدمات
    rows = cur.execute("SELECT id, title, estimated_time FROM services").fetchall()
    if not rows:
        sys.exit("هیچ خدمتی در جدول services پیدا نشد.")

    # محاسبه تغییرات لازم
    updates: list[tuple[str, str]] = []  # (new_value, id)
    for service_id, _title, est_time in rows:
        new_value = new_value_for(est_time)
        if new_value is not None and new_value != est_time:
            updates.append((new_value, service_id))

    print(f"تعداد کل خدمات: {len(rows)}")
    print(f"خدماتی که باید تغییر کنند: {len(updates)}\n")

    # نمایش چند نمونه قبل/بعد
    print("--- نمونه‌های قبل / بعد ---")
    for service_id, _title, est_time in rows[:5]:
        new_value = new_value_for(est_time) or est_time
        print(f"  {est_time!r}  →  {new_value!r}")
    print()

    if not updates:
        print("هیچ خدمتی نیاز به تغییر ندارد؛ همه مقادیر از قبل درست هستند.")
        con.close()
        return

    if dry_run:
        print("پیش‌نمایش کامل شد. برای اعمال واقعی، بدون --dry-run اجرا کنید.")
        con.close()
        return

    # بکاپ و اعمال تغییرات
    backup_path = make_backup(db_path)
    print(f"بکاپ ساخته شد: {backup_path.name}")

    try:
        cur.execute("BEGIN")
        cur.executemany(
            "UPDATE services SET estimated_time = ? WHERE id = ?",
            updates,
        )
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        sys.exit("خطا هنگام به‌روزرسانی! تغییرات برگردانده شد (rollback).")

    # تأیید نهایی
    remaining = cur.execute(
        "SELECT COUNT(*) FROM services WHERE estimated_time LIKE '%ساعت%'"
    ).fetchone()[0]
    new_count = cur.execute(
        "SELECT COUNT(*) FROM services WHERE estimated_time = ?", (NEW_VALUE,)
    ).fetchone()[0]
    con.close()

    print("\n--- نتیجه ---")
    print(f"تعداد ردیف‌های به‌روزشده: {len(updates)}")
    print(f"خدماتی که اکنون «{NEW_VALUE}» دارند: {new_count}")
    print(f"ردیف‌های باقی‌مانده شامل «ساعت»: {remaining}")
    if remaining == 0:
        print("✅ تغییرات با موفقیت روی همه خدمات اعمال شد.")
    else:
        print("⚠️ چند ردیف هنوز شامل «ساعت» هستند (احتمالاً داده متفاوتی دارند).")


if __name__ == "__main__":
    main()
