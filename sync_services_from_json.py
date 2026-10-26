#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_services_from_json.py  (نسخه جایگزینی کامل + دسته‌بندی موضوعی + آیکن و رنگ)
===============================================================================
۱) همه خدمات قبلیِ جدول services دیتابیس حذف می‌شوند
۲) همه خدمات فایل JSON از نو اضافه می‌شوند:
     - هر خدمت در «دسته‌بندی موضوعی» خودش قرار می‌گیرد (تشخیص با کلیدواژه فارسی)
     - برای هر خدمت «آیکن» و «رنگ» مناسب انتخاب می‌شود

فیلدهایی که از JSON وارد می‌شوند (فقط همین‌ها):
    price, description, noticeType, noticeText, title, benefit

نگاشت به ستون‌های دیتابیس:
    price       →  base_price   (ستون موجود)
    description →  description  (ستون موجود)
    title       →  title        (ستون موجود)
    noticeType  →  notice_type  (ستون جدید - در صورت نبود خودکار ساخته می‌شود)
    noticeText  →  notice_text  (ستون جدید - در صورت نبود خودکار ساخته می‌شود)
    benefit     →  benefit      (ستون جدید - در صورت نبود خودکار ساخته می‌شود)

نکته: ستون estimated_time (زمان انجام کار) همان مقدار قبلی دیتابیس است.

نحوه اجرا:
    python sync_services_from_json.py                       # فایل‌ها را خودکار پیدا می‌کند
    python sync_services_from_json.py --dry-run             # فقط پیش‌نمایش، بدون تغییر
    python sync_services_from_json.py --preview             # نمایش دسته/آیکن/رنگ هر خدمت
    python sync_services_from_json.py services.json db.sqlite
"""

import hashlib
import json
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# ------------------------------------------------- تنظیمات نگاشت فیلدها ----
FIELD_MAP = {
    "price": "base_price",         # موجود
    "description": "description",  # موجود
    "title": "title",              # موجود
    "noticeType": "notice_type",   # جدید
    "noticeText": "notice_text",   # جدید
    "benefit": "benefit",          # جدید
}

NEW_COLUMNS = {
    "notice_type": "TEXT",
    "notice_text": "TEXT",
    "benefit": "INTEGER",
}

DEFAULT_ESTIMATED_TIME_FALLBACK = "۳۰ الی ۶۰ دقیقه"

# -------------------------------------------- دسته‌بندی موضوعی خدمات -------
# قواعد به ترتیب اولویت هستند؛ اولین قاعده‌ای که کلیدواژه‌ای در عنوان
# پیدا کند، دسته را تعیین می‌کند.
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    # عمومی
    ("cat-amvmi-bdvn-dsthbndi-mshkhs", ["درخواست عمومی"]),
    # تامین اجتماعی، بازنشستگی و مستمری (قبل از پلیس تا «بیمه روستایی سربازی» درست دسته شود)
    ("cat-tamin-ajtmaai", [
        "بیمه", "فیش", "حقوق", "بازنشستگ", "مستمری", "صندوق",
        "فرهنگیان", "وضعیت حیات", "از کار افتادگی",
    ]),
    # پلیس، گذرنامه، نظام وظیفه
    ("cat-khdmat-plis", [
        "پلیس", "پلیس اماکن", "گذرنامه", "پاسپورت", "سربازی",
        "پایان خدمت", "معافیت", "مرزنشین", "خروج از کشور", "سرقت",
        "مکشوفه", "کلاهبرداری", "تبعید", "عدم سو پیشینه", "بلاصاحب",
    ]),
    # مالیاتی
    ("cat-khdmat-maliati", ["مالیات", "پرسشنامه", "پلمپ", "کد اقتصادی", "کد نقش"]),
    # عوارض آزادراه مربوط به راهور است نه شهرداری
    ("cat-khvdrv-v-rahvr", ["آزاد راه", "آزادراه"]),
    # دادگستری و ثنا
    ("cat-dadgstri-v-sna", [
        "ثنا", "دادگاه", "دادگستری", "قضایی", "طلاق", "تجرد",
        "وراثت", "عریضه", "دادرسی", "روند پرونده",
    ]),
    # قبوض، انشعابات (برق، گاز، آب) و شهرداری
    ("cat-shhrdari-v-ghbvz", [
        "برق", "گاز", "کنتور", "انشعاب", "قبض", "قبوض", "صورت حساب",
        "وصل مجدد", "قدرت", "ساکنان", "مشترک", "تسویه حساب",
        "شهرداری", "عوارض", "سد معبر",
    ]),
    # یارانه و کالابرگ
    ("cat-khdmat-iaranh-v-kalabrg", ["یارانه", "کالابرگ", "دهک", "خانوار"]),
    # حمایت و کمیته امداد
    ("cat-iaranh-v-hmait", ["کمیته", "مددجو", "نیازمند"]),
    # خدمات بانکی
    ("cat-khdmat-banki", [
        "بانک", "شبا", "چک", "سهام", "سجام", "اعتبارسنج", "اعتبار",
        "اقساط", "وام", "کارتخوان", "رسالت", "مهرایران", "تطبیق",
        "تبدیل", "ضمانت", "حساب", "دریافت ارز", "معدل",
    ]),
    # تامین اجتماعی، بازنشستگی و مستمری
    ("cat-tamin-ajtmaai", [
        "بیمه", "فیش", "حقوق", "بازنشستگ", "مستمری", "صندوق",
        "فرهنگیان", "وضعیت حیات", "از کار افتادگی",
    ]),
    # خدمات خودرویی (سوخت، لاستیک، ...)
    ("cat-khdmat-khvdrvii", [
        "کارت سوخت", "سوخت", "لاستیک", "دوگانه", "دو گانه",
        "فرسوده", "نفتی", "سهمیه",
    ]),
    # خودرو و راهور
    ("cat-khvdrv-v-rahvr", [
        "خودرو", "موتور", "پلاک", "گواهینامه", "خلافی", "ترافیک",
        "راهور", "تصادف", "کروکی", "ترخیص", "پارکینگ", "تاکسی",
        "رانندگی", "نمره منفی", "ماشین",
    ]),
    # ثبت احوال (قبل از مسکن تا «سرشماری نفوس و مسکن» درست دسته شود)
    ("cat-sbt-ahval", ["کد ملی", "کارت ملی", "شناسنامه", "ثبت احوال", "سرشماری"]),
    # مسکن و املاک
    ("cat-mskn-v-amlak", [
        "مسکن", "ملک", "املاک", "اجاره", "سند", "زمین", "اسکان", "مالکیت",
    ]),
    # خدمات پستی
    ("cat-khdmat-psti", ["پست", "مرسوله", "کد پستی", "کدپستی", "شهاب"]),
    # مخابرات و موبایل
    ("cat-mkhabrat-v-mvbail", [
        "مکالمات", "همراه اول", "سیم کارت", "فکس", "گوشی", "رجیستری", "موبایل",
    ]),
    # سلامت و درمان
    ("cat-slamt-v-drman", ["دکتر", "مطب", "پزشک", "درمان", "دارو", "بهداشت", "سلامت"]),
    # خدمات دانشجویی
    ("cat-khdmat-danshjvii", ["دانشگاه", "دانشجو", "واحد", "جزوه", "بورسیه"]),
    # سازمان سنجش
    ("cat-khdmat-sazman-snjsh-v-astkhdami", ["سنجش"]),
    # کار و استخدام
    ("cat-kar-v-astkhdam", ["استخدام"]),
    # آموزش و آزمون
    ("cat-amvzsh-v-azmvn", [
        "مدرسه", "مدارس", "دانش آموز", "کنکور", "آزمون", "کارنامه",
        "کتاب درسی", "سمپاد", "نمونه دولتی", "فنی حرفه", "فنی و حرفه",
        "امتحان", "کلاس اول", "تحصیلی", "سخا", "درسی", "جلسه",
    ]),
    # چاپ و طراحی
    ("cat-chap-v-trahi", ["طراحی", "چاپ", "مهر", "بنر", "لوگو", "عکس", "برند", "علامت تجاری"]),
    # تایپ و صفحه‌آرایی
    ("cat-taip-v-sfhharaii", ["تایپ", "نامه اداری", "صفحه آرایی"]),
    # کامپیوتر و ویندوز
    ("cat-kampivtr-v-vindvz", ["ایمیل", "ویندوز", "کامپیوتر", "تحقیق", "نصب"]),
    # سایر خدمات (ثبت شرکت، سفر و زیارت، ...)
    ("cat-sair-khdmat", [
        "ثبت شرکت", "شرکت", "انبار", "معدن", "فاکتور", "جواز کسب",
        "امضا الکترونیک", "اصالت اسناد", "اصناف",
        "بلیط", "تور", "گردشگری", "هواپیما", "اتوبوس", "سینما",
        "حج", "کربلا", "اقامتی", "تعبیر",
    ]),
]
CATEGORY_FALLBACK = "cat-sair-khdmat"

# -------------------------------------------- آیکن و رنگ خدمات -------------
# آیکن پیش‌فرض هر دسته (از آیکن‌های استفاده‌شده در نسخه قبلی دیتابیس)
CATEGORY_ICONS = {
    "cat-amvmi-bdvn-dsthbndi-mshkhs": "bell",
    "cat-khdmat-plis": "user-shield",
    "cat-khdmat-maliati": "file-invoice-dollar",
    "cat-shhrdari-v-ghbvz": "file-invoice-dollar",
    "cat-dadgstri-v-sna": "gavel",
    "cat-khdmat-iaranh-v-kalabrg": "basket-shopping",
    "cat-iaranh-v-hmait": "hand-holding-heart",
    "cat-khdmat-banki": "money-bill-wave",
    "cat-tamin-ajtmaai": "umbrella",
    "cat-khdmat-khvdrvii": "screwdriver-wrench",
    "cat-khvdrv-v-rahvr": "car",
    "cat-mskn-v-amlak": "house",
    "cat-khdmat-psti": "truck-fast",
    "cat-mkhabrat-v-mvbail": "mobile-screen-button",
    "cat-sbt-ahval": "id-card",
    "cat-slamt-v-drman": "stethoscope",
    "cat-khdmat-danshjvii": "user-graduate",
    "cat-khdmat-sazman-snjsh-v-astkhdami": "school",
    "cat-kar-v-astkhdam": "briefcase",
    "cat-amvzsh-v-azmvn": "graduation-cap",
    "cat-chap-v-trahi": "pen-nib",
    "cat-taip-v-sfhharaii": "keyboard",
    "cat-kampivtr-v-vindvz": "screwdriver-wrench",
    "cat-sair-khdmat": "ellipsis",
}

# قواعد اختصاصی آیکن (اولین تطبیق برنده می‌شود)
ICON_RULES: list[tuple[str, list[str]]] = [
    ("magnifying-glass", ["استعلام"]),
    ("file-invoice-dollar", ["قبض", "قبوض", "صورت حساب", "فیش", "حکم"]),
    ("umbrella", ["بیمه"]),
    ("hand-holding-dollar", ["وام", "اقساط", "مستمری", "یارانه", "ودیعه"]),
    ("screwdriver-wrench", ["کنتور", "انشعاب", "دوگانه", "دو گانه", "لاستیک", "نصب", "تعمیر"]),
    ("money", ["چک", "شبا", "تطبیق", "تبدیل", "حساب", "کارتخوان"]),
    ("percent", ["سهام", "سجام", "اعتبار"]),
    ("gavel", ["دادگاه", "قضایی", "ثنا", "طلاق", "وراثت", "عریضه", "دادرسی"]),
    ("passport", ["گذرنامه", "پاسپورت"]),
    ("id-card", ["کارت ملی", "کد ملی", "شناسنامه", "گواهینامه", "مرزنشین"]),
    ("calendar-check", ["نوبت", "بلیط", "رزرو", "جلسه"]),
    ("clipboard-check", ["ثبت نام", "ثبتنام", "درخواست", "ثبت ", "عضویت"]),
    ("mobile-screen-button", ["موبایل", "گوشی", "مکالمات", "سیم کارت", "فکس"]),
    ("truck-fast", ["مرسوله", "پست", "ارسال"]),
    ("keyboard", ["تایپ", "نامه اداری", "صفحه آرایی"]),
    ("pen-nib", ["طراحی", "مهر", "لوگو", "بنر", "امضا"]),
    ("print", ["چاپ"]),
    ("image", ["عکس"]),
    ("car-burst", ["تصادف", "سرقت", "فرسوده", "خلافی", "کروکی", "مکشوفه"]),
    ("car", ["خودرو", "موتور", "ماشین", "پلاک", "رانندگی", "سوخت", "تاکسی", "ترافیک"]),
    ("house", ["مسکن", "ملک", "املاک", "سند", "زمین", "مالکیت", "اسکان"]),
    ("house-chimney", ["اجاره", "ساکنان"]),
    ("stethoscope", ["دکتر", "مطب", "پزشک", "درمان", "دارو", "بهداشت", "سلامت"]),
    ("user-graduate", ["دانشگاه", "دانشجو", "بورسیه"]),
    ("school", ["مدرسه", "مدارس", "کنکور", "آزمون", "سمپاد", "سنجش", "نمونه دولتی", "استخدام"]),
    ("graduation-cap", ["دانش آموز", "تحصیلی", "کارنامه", "کتاب درسی", "فنی حرفه", "فنی و حرفه", "درسی"]),
    ("briefcase", ["شرکت", "جواز", "انبار", "معدن", "فاکتور", "اصناف"]),
    ("basket-shopping", ["کالابرگ", "خرید", "سوخت"]),
    ("hand-holding-heart", ["کمیته", "مددجو", "نیازمند", "خانوار"]),
    ("user-shield", ["پلیس", "سو پیشینه", "اماکن"]),
    ("scale-balanced", ["دادگستری", "اعتراض", "تفکیک", "تعاونی"]),
    ("wifi", ["اینترنت", "وای فای"]),
    ("file-pdf", ["پی دی اف", "pdf"]),
    ("file-word", ["ورد", "word"]),
    ("file-excel", ["اکسل", "excel"]),
    ("building-columns", ["بانک", "صندوق", "دادگاه"]),
    ("notes-medical", ["نسخه", "پرونده پزشکی", "دارویاب"]),
    ("heart-pulse", ["سلامت", "درمان", "بهداشت"]),
    ("bell", ["اعلام", "اطلاع", "پیگیری"]),
    ("folder-open", ["مدارک", "مفقودی", "بارگذاری", "بررسی"]),
    ("file-lines", ["فایل", "جزوه", "سوالات", "تحقیق"]),
    ("file-pen", ["نامه", "اظهارنامه", "تنظیم", "ویرایش"]),
]

# پالت رنگ موضوعی هر دسته (از رنگ‌های استفاده‌شده در نسخه قبلی دیتابیس)
CATEGORY_COLORS = {
    "cat-amvmi-bdvn-dsthbndi-mshkhs": ["#2f80ed", "#0ca67d", "#f6c344"],
    "cat-khdmat-plis": ["#2f80ed", "#0ca67d", "#7c5cff"],
    "cat-khdmat-maliati": ["#f6c344", "#e75b2e", "#6f42c1"],
    "cat-shhrdari-v-ghbvz": ["#f6c344", "#10aec8", "#0ca67d"],
    "cat-dadgstri-v-sna": ["#6f42c1", "#2f80ed", "#0ca67d"],
    "cat-khdmat-iaranh-v-kalabrg": ["#40a83b", "#f6c344", "#0ca67d"],
    "cat-iaranh-v-hmait": ["#e75b2e", "#d63384", "#40a83b"],
    "cat-khdmat-banki": ["#10aec8", "#2f80ed", "#40a83b", "#0ca67d"],
    "cat-tamin-ajtmaai": ["#0dcaf0", "#ef6c7b", "#40a83b", "#2f80ed"],
    "cat-khdmat-khvdrvii": ["#f08d37", "#10aec8", "#e75b2e"],
    "cat-khvdrv-v-rahvr": ["#2f80ed", "#ef6c7b", "#f08d37"],
    "cat-mskn-v-amlak": ["#2f80ed", "#e75b2e", "#7c5cff", "#d63384"],
    "cat-khdmat-psti": ["#f08d37", "#10aec8"],
    "cat-mkhabrat-v-mvbail": ["#40a83b", "#7c5cff", "#e75b2e", "#2f80ed"],
    "cat-sbt-ahval": ["#7c5cff", "#2f80ed", "#f6c344", "#10aec8"],
    "cat-slamt-v-drman": ["#40a83b", "#d63384", "#0ca67d", "#ef6c7b"],
    "cat-khdmat-danshjvii": ["#d63384", "#10aec8", "#6f42c1"],
    "cat-khdmat-sazman-snjsh-v-astkhdami": ["#6f42c1", "#0dcaf0", "#f08d37"],
    "cat-kar-v-astkhdam": ["#10aec8", "#40a83b", "#f08d37"],
    "cat-amvzsh-v-azmvn": ["#f08d37", "#7c5cff", "#40a83b", "#0dcaf0"],
    "cat-chap-v-trahi": ["#d63384", "#6f42c1", "#0dcaf0", "#f6c344"],
    "cat-taip-v-sfhharaii": ["#7c5cff", "#2f80ed", "#0dcaf0"],
    "cat-kampivtr-v-vindvz": ["#10aec8", "#2f80ed", "#7c5cff"],
    "cat-sair-khdmat": ["#40a83b", "#7c5cff", "#e75b2e", "#f08d37"],
}


# ------------------------------------------------------------ توابع کمکی ---
def normalize(text: str) -> str:
    """نرمال‌سازی متن فارسی برای تطبیق مطمئن کلیدواژه‌ها."""
    text = text.replace("\u200c", "").replace("\u200f", "").replace("\u200e", "")
    text = text.replace("ي", "ی").replace("ك", "ک")
    for fa, en in zip("۰۱۲۳۴۵۶۷۸۹", "0123456789"):
        text = text.replace(fa, en)
    text = " ".join(text.split()).strip().lower()
    return text


def classify(title: str) -> str:
    """تعیین دسته موضوعی خدمت بر اساس کلیدواژه‌های عنوان."""
    t = normalize(title)
    for category_id, keywords in CATEGORY_RULES:
        for kw in keywords:
            if normalize(kw) in t:
                return category_id
    return CATEGORY_FALLBACK


def pick_icon(title: str, category_id: str) -> str:
    """انتخاب آیکن خدمت: اولین قاعده تطبیقی، وگرنه آیکن پیش‌فرض دسته."""
    t = normalize(title)
    for icon, keywords in ICON_RULES:
        for kw in keywords:
            if normalize(kw) in t:
                return icon
    return CATEGORY_ICONS.get(category_id, "bell")


def pick_color(title: str, category_id: str) -> str:
    """انتخاب رنگ از پالت موضوعی دسته (به‌صورت قطعی و پایدار با هش عنوان)."""
    palette = CATEGORY_COLORS.get(category_id, ["#2f80ed", "#40a83b", "#f6c344"])
    digest = hashlib.md5(title.encode("utf-8")).hexdigest()
    return palette[int(digest, 16) % len(palette)]


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
    """خواندن لیست خدمات از فایل JSON."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    items = data["items"] if isinstance(data, dict) and "items" in data else data
    if not isinstance(items, list):
        sys.exit("خطا: ساختار JSON قابل قبول نیست (لیست خدمات پیدا نشد).")
    return items


def ensure_columns(cur: sqlite3.Cursor) -> list[str]:
    """ساخت ستون‌های ناقص در جدول services."""
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
    row = cur.execute(
        "SELECT estimated_time, COUNT(*) AS c FROM services "
        "GROUP BY estimated_time ORDER BY c DESC LIMIT 1"
    ).fetchone()
    estimated_time = row[0] if row and row[0] else DEFAULT_ESTIMATED_TIME_FALLBACK
    return {"estimated_time": estimated_time}


# ------------------------------------------------------------------ main ---
def main() -> None:
    dry_run = "--dry-run" in sys.argv
    preview = "--preview" in sys.argv
    json_path, db_path = find_files(sys.argv[1:])
    print(f"فایل JSON : {json_path.resolve()}")
    print(f"دیتابیس   : {db_path.resolve()}")

    items = load_json_items(json_path)

    # محاسبه دسته/آیکن/رنگ برای همه خدمات
    planned = []
    for i, it in enumerate(items):
        cat = classify(it["title"])
        planned.append(
            {
                "item": it,
                "category": cat,
                "icon": pick_icon(it["title"], cat),
                "color": pick_color(it["title"], cat),
                "sort": i,
            }
        )

    # گزارش توزیع دسته‌ها
    cat_counter = Counter(p["category"] for p in planned)
    print(f"\n--- توزیع {len(items)} خدمت در دسته‌بندی‌های موضوعی ---")
    for cat, n in cat_counter.most_common():
        print(f"  {cat}: {n}")

    if preview:
        print("\n--- پیش‌نمایش کامل (عنوان | دسته | آیکن | رنگ) ---")
        for p in planned:
            print(f"  {p['item']['title']} | {p['category']} | {p['icon']} | {p['color']}")
        return

    if dry_run:
        print("\nحالت پیش‌نمایش (dry-run): هیچ تغییری ذخیره نخواهد شد.")
        print("نمونه (اولین ۵ مورد):")
        for p in planned[:5]:
            print(f"  + {p['item']['title']}  →  {p['category']} | {p['icon']} | {p['color']}")
        return

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    defaults = pick_default_values(cur)
    old_count = cur.execute("SELECT COUNT(*) FROM services").fetchone()[0]
    orders_ref = cur.execute(
        "SELECT COUNT(*) FROM orders WHERE service_id IS NOT NULL"
    ).fetchone()[0]

    print(f"\nخدمات فعلی دیتابیس: {old_count}  ← همه حذف می‌شوند")
    print(f"خدمات JSON: {len(items)}  ← همه اضافه می‌شوند")
    print(f"زمان انجام کار خدمات جدید: {defaults['estimated_time']!r}")
    print(f"سفارش‌های تاریخی که به خدمات اشاره می‌کنند (حفظ می‌شوند): {orders_ref}")

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

        # ۲) افزودن همه خدمات JSON با دسته، آیکن و رنگ
        insert_rows = []
        for p in planned:
            it = p["item"]
            slug = (it["title"] or "").replace(" ", "-")
            insert_rows.append(
                (
                    f"srv-{it['id']}",                      # id
                    p["category"],                           # category_id (موضوعی)
                    it["title"],                             # title
                    it.get("description"),                   # description
                    it.get("price"),                         # base_price
                    defaults["estimated_time"],              # estimated_time (مقدار قبلی دیتابیس)
                    p["icon"],                               # icon
                    1,                                       # is_active
                    "[]",                                    # fields_json
                    "",                                      # pricing_formula
                    p["color"],                              # icon_color
                    p["sort"],                               # sort_order (ترتیب JSON)
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
    no_cat = cur.execute(
        "SELECT COUNT(*) FROM services WHERE category_id IS NULL OR category_id = ''"
    ).fetchone()[0]
    no_icon = cur.execute(
        "SELECT COUNT(*) FROM services WHERE icon IS NULL OR icon = ''"
    ).fetchone()[0]
    no_color = cur.execute(
        "SELECT COUNT(*) FROM services WHERE icon_color IS NULL OR icon_color = ''"
    ).fetchone()[0]
    dist = cur.execute(
        "SELECT category_id, COUNT(*) FROM services GROUP BY category_id ORDER BY COUNT(*) DESC"
    ).fetchall()
    con.close()

    print("\n--- نتیجه ---")
    print(f"خدمات قبلی حذف‌شده: {old_count}")
    print(f"خدمات جدید اضافه‌شده: {total} (انتظار: {len(items)})")
    print(f"بدون دسته: {no_cat} | بدون آیکن: {no_icon} | بدون رنگ: {no_color}")
    for cat, n in dist:
        print(f"  {cat}: {n}")
    if total == len(items) and no_cat == 0 and no_icon == 0 and no_color == 0:
        print("✅ جایگزینی کامل خدمات با دسته‌بندی موضوعی، آیکن و رنگ با موفقیت انجام شد.")
    else:
        print("⚠️ مشکلی وجود دارد؛ فایل بکاپ را بررسی کنید.")


if __name__ == "__main__":
    main()
