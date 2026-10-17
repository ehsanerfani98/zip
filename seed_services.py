#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
seed_services.py
================
اسکریپت ایجاد دسته‌بندی‌ها و خدمات کافی‌نت از روی فایل cn.md و اتصال هر خدمت
به دسته‌بندی مربوطه در دیتابیس caffenet.sqlite

کارایی اسکریپت (هر بار که اجرا شود):
  1) از فایل cn.md، دسته‌بندی‌ها (خطوط **دسته: ...** و **...**) و خدمات
     (خطوط * ...) را می‌خواند.
  2) یک نسخه پشتیبان از دیتابیس می‌گیرد (caffenet.sqlite.backup-<زمان>).
  3) دسته‌بندی‌ها را در جدول categories ایجاد می‌کند (با آیکون Font Awesome 6).
  4) خدمات را در جدول services ایجاد می‌کند و با category_id به دسته‌بندی
     مربوطه متصل می‌کند.
  5) آیکون‌های ردیف‌های قدیمی (۴ دسته و ۴ خدمت اولیه) را به نام‌های
     Font Awesome تبدیل می‌کند.
  6) ردیف‌های تکراری ایجاد نمی‌کند؛ اجرای مجدد اسکریپت امن است (Idempotent).

نکات مهم:
  - آیکون‌ها همگی از Font Awesome 6 (نسخه رایگان، استایل Solid) هستند و بدون
    پیشوند «fa-» ذخیره می‌شوند (مثلاً: print، graduation-cap، building-columns).
  - قیمت پایه خدمات جدید به‌صورت پیش‌فرض ۵۰,۰۰۰ تومان است؛ با گزینه --price
    قابل تغییر است و در اجرای مجدد، قیمت‌های ویرایش‌شده دستی بازنویسی نمی‌شوند.
  - خدمات جدید یک فیلد فرم عمومی (توضیحات اپراتور) دارند که بعداً از پنل
    مدیریت قابل تکمیل است.

روش اجرا:
    python seed_services.py                       # حالت عادی (کنار فایل‌های پروژه)
    python seed_services.py --dry-run             # فقط نمایش خروجی، بدون تغییر دیتابیس
    python seed_services.py --db path/to/caffenet.sqlite --source path/to/cn.md
    python seed_services.py --price 80000         # قیمت پایه دلخواه برای خدمات جدید
    python seed_services.py --no-backup           # بدون تهیه بکاپ
    python seed_services.py --no-icon-fix         # بدون تغییر آیکون ردیف‌های قدیمی
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB = os.path.join(SCRIPT_DIR, 'caffenet.sqlite')
DEFAULT_SOURCE = os.path.join(SCRIPT_DIR, 'cn.md')

# ----------------------------------------------------------------------
# تنظیمات قابل تغییر
# ----------------------------------------------------------------------
DEFAULT_BASE_PRICE = 50000            # قیمت پایه خدمات جدید (تومان)
DEFAULT_ESTIMATED_TIME = '۲ الی ۲۴ ساعت'

# پالت رنگ آیکون‌ها (چرخه‌ای بین دسته‌بندی‌ها توزیع می‌شود)
PALETTE = ['#0ca67d', '#ef6c7b', '#10aec8', '#f08d37', '#7c5cff', '#2f80ed',
           '#e75b2e', '#40a83b', '#d63384', '#0dcaf0', '#f6c344', '#6f42c1']

# آیکون پیش‌فرض اگر دسته‌بندی در لیست زیر نباشد
FALLBACK_ICON = 'shapes'

# آیکون و توضیح هر دسته‌بندی — کلیدها دقیقاً مطابق عناوین فایل cn.md هستند
CATEGORY_META = {
    # ---------------- گروه اول (بخش اصلی فایل) ----------------
    'عمومی / بدون دسته‌بندی مشخص': (
        'folder-open', 'خدمات متفرقه و عمومی کافی‌نت شامل استعلام‌ها، ابلاغیه‌ها و مدارک گوناگون'),
    'دادگستری و ثنا': (
        'scale-balanced', 'ثبت‌نام ثنا، شکایات و لوایح قضایی، ابلاغیه‌ها و پیگیری پرونده‌های دادگستری'),
    'تأمین اجتماعی': (
        'hand-holding-heart', 'سوابق بیمه، فیش حقوقی، غرامت و خدمات سازمان تأمین اجتماعی'),
    'خودرو و راهور': (
        'car', 'گواهینامه، پلاک، کارت سوخت، طرح ترافیک و خدمات راهور'),
    'ثبت احوال': (
        'id-card', 'شناسنامه، پاسپورت، کارت ملی هوشمند و گواهی‌های ثبت احوال'),
    'آموزش و آزمون': (
        'graduation-cap', 'ثبت‌نام آزمون‌ها، نتایج، کارت ورود به جلسه و خدمات آموزشی'),
    'یارانه و حمایت': (
        'hand-holding-dollar', 'یارانه نقدی، دهک‌بندی، کالابرگ الکترونیکی و درخواست‌های حمایتی'),
    'مسکن و املاک': (
        'house', 'قرارداد اجاره و خرید ملک، سامانه املاک و اسکان و تسهیلات مسکن'),
    'سلامت و درمان': (
        'heart-pulse', 'بیمه سلامت، نوبت‌دهی پزشک، نسخه الکترونیکی و خسارت درمانی'),
    'کار و استخدام': (
        'briefcase', 'اداره کار، بیمه بیکاری، مشاغل خانگی و سامانه‌های اشتغال'),
    'شهرداری و قبوض': (
        'file-invoice-dollar', 'تغییر نام انشعابات، قبوض آب، برق، گاز و عوارض شهرداری'),
    'مخابرات و موبایل': (
        'mobile-screen-button', 'همتا، رجیستری گوشی، تلفن ثابت و اینترنت'),
    'فایل و مدارک': (
        'copy', 'تایپ، تبدیل و آماده‌سازی فایل‌ها و مدارک'),
    'استعلام‌های سیستمی': (
        'magnifying-glass-chart', 'استعلام‌های آنی و سیستمی بانکی'),
    'خدمات پستی': (
        'envelope', 'کد پستی، رهگیری مرسولات و خدمات سخا'),
    'تایپ و صفحه‌آرایی': (
        'keyboard', 'تایپ حرفه‌ای فارسی و انگلیسی، صفحه‌آرایی و ویرایش فایل Word'),
    'PDF، عکس و مدارک': (
        'file-pdf', 'تبدیل، ادغام و فشرده‌سازی PDF و ویرایش عکس'),
    'چاپ و طراحی': (
        'pen-ruler', 'طراحی کارت ویزیت، تراکت، پاورپوینت و ورود اطلاعات'),
    'کامپیوتر و ویندوز': (
        'laptop', 'نصب نرم‌افزار و رفع مشکلات ویندوز از راه دور'),
    # ---------------- گروه دوم (منوی دسته‌بندی) ----------------
    'خدمات خودرویی': (
        'car-side', 'خلافی، عوارض، پلاک، پروانه ناوگان و ثبت‌نام‌های خودرو'),
    'خدمات مالیاتی': (
        'percent', 'اظهارنامه‌ها، کد اقتصادی و تبصره ماده ۱۰۰'),
    'خدمات یارانه و کالابرگ': (
        'basket-shopping', 'جداسازی یارانه، خانوار هدفمندی و کالابرگ الکترونیکی'),
    'خدمات قضایی و دادگستری': (
        'gavel', 'ابلاغیه قضایی، عدم سوءپیشینه و پیگیری پرونده'),
    'خدمات سازمان سنجش و استخدامی': (
        'clipboard-list', 'کارت آزمون، کنکورها و آزمون‌های استخدامی'),
    'خدمات مسکن و زمین': (
        'house-chimney', 'مسکن ملی، املاک و اسکان و وام‌های ساخت'),
    'خدمات بانکی': (
        'building-columns', 'وام ازدواج و فرزند، چک صیادی، اعتبارسنجی و تسهیلات بانکی'),
    'خدمات پلیس': (
        'shield-halved', 'گذرنامه، پلاک، معافیت‌ها و مجوزهای پلیس'),
    'خدمات تامین اجتماعی': (
        'notes-medical', 'سوابق، قرارداد، استعلاجی و مستمری تامین اجتماعی'),
    'خدمات دانشجویی': (
        'user-graduate', 'انتخاب واحد، شهریه، تاییدیه تحصیلی و دانشگاه آزاد'),
    'پروانه کسب و مجوز': (
        'certificate', 'پروانه کسب، کد اقتصادی، تاییدیه اماکن و مجوزها'),
    'خدمات دانش آموزی': (
        'school', 'کتاب درسی، سنجش نوآموزان و خدمات مدارس'),
    'مشاوره': (
        'comments', 'مشاوره تخصصی تامین اجتماعی، مالیات، اداره کار و تحصیلی'),
    'سایر خدمات': (
        'ellipsis', 'خدمات متفرقه بازنشستگان کشوری، سجام، بیمه‌ها و سایر سامانه‌ها'),
    # ---------------- دسته‌بندی‌های قدیمی موجود در دیتابیس ----------------
    'پرینت، کپی و اسکن مدارک': (
        'print', 'چاپ سیاه و سفید و رنگی انواع اسناد، کتاب، پایان‌نامه، کاتالوگ و اسکن با کیفیت بالا'),
    'دانشجویی و ثبت‌نام مدارس': (
        'graduation-cap', 'کنکور سراسری، آزمون‌های سنجش، تاییدیه تحصیلی، دیپلم و انتخاب رشته آنلاین'),
    'خدمات دولتی و اداری': (
        'building-columns', 'سوابق بیمه تامین اجتماعی، فیش حقوقی، سامانه ثنا، نوبت‌دهی تعویض پلاک و معاینه فنی'),
    'مالیاتی، وام و اصناف': (
        'landmark', 'اظهارنامه مالیاتی، ثبت‌نام وام ازدواج و فرزندآوری، جواز کسب، سامانه سجام و بورس'),
}

# قواعد انتخاب آیکون خدمات بر اساس کلیدواژه (اولین تطبیق برنده است)
SERVICE_ICON_RULES = [
    ('استعلام', 'magnifying-glass'),
    ('خلافی', 'car-burst'),
    ('پرداخت', 'money-bill-wave'),
    ('عوارض', 'money-bill-wave'),
    ('وام', 'hand-holding-dollar'),
    ('تسهیلات', 'hand-holding-dollar'),
    ('بیمه', 'umbrella'),
    ('گذرنامه', 'passport'),
    ('پاسپورت', 'passport'),
    ('گواهینامه', 'id-card'),
    ('شناسنامه', 'address-card'),
    ('کارت ملی', 'address-card'),
    ('ثنا', 'scale-balanced'),
    ('شکایت', 'gavel'),
    ('شکوائیه', 'gavel'),
    ('دادخواست', 'gavel'),
    ('طلاق', 'scale-balanced'),
    ('ابلاغیه', 'bell'),
    ('تایپ', 'keyboard'),
    ('ترجمه', 'language'),
    ('رزومه', 'file-lines'),
    ('چاپ', 'print'),
    ('پرینت', 'print'),
    ('عکس', 'image'),
    ('PDF', 'file-pdf'),
    ('Word', 'file-word'),
    ('Excel', 'file-excel'),
    ('نوبت', 'calendar-check'),
    ('نتایج', 'clipboard-check'),
    ('قبض', 'file-invoice-dollar'),
    ('فیش', 'file-invoice-dollar'),
    ('حقوقی', 'file-invoice-dollar'),
    ('سیم', 'mobile-screen-button'),
    ('موبایل', 'mobile-screen-button'),
    ('گوشی', 'mobile-screen-button'),
    ('همتا', 'mobile-screen-button'),
    ('اینترنت', 'wifi'),
    ('اجاره', 'house-chimney'),
    ('ملک', 'house'),
    ('مسکن', 'house'),
    ('یارانه', 'basket-shopping'),
    ('کالابرگ', 'basket-shopping'),
    ('کنکور', 'graduation-cap'),
    ('دانشگاه', 'user-graduate'),
    ('دانشجو', 'user-graduate'),
    ('ارشد', 'graduation-cap'),
    ('دکتری', 'graduation-cap'),
    ('انتخاب رشته', 'list-check'),
    ('مدرسه', 'school'),
    ('دانش آموز', 'school'),
    ('سربازی', 'user-shield'),
    ('نظام وظیفه', 'user-shield'),
    ('ثبت', 'file-pen'),
    ('درخواست', 'file-pen'),
    ('پستی', 'truck-fast'),
    ('مرسوله', 'truck-fast'),
    ('کد پستی', 'map-location-dot'),
    ('ویندوز', 'screwdriver-wrench'),
    ('نصب', 'screwdriver-wrench'),
    ('رفع', 'screwdriver-wrench'),
    ('طراحی', 'pen-nib'),
    ('پاورپوینت', 'pen-nib'),
    ('مشاوره', 'comments'),
    ('درمان', 'stethoscope'),
    ('پزشک', 'stethoscope'),
    ('سلامت', 'stethoscope'),
    ('مالیات', 'percent'),
    ('چک', 'money-check-pen'),
    ('حساب', 'building-columns'),
    ('شبا', 'building-columns'),
    ('کد اقتصادی', 'percent'),
    ('اظهارنامه', 'file-lines'),
    ('انشعاب', 'faucet'),
    ('کنتور', 'gauge-high'),
]

# اصلاح آیکون ردیف‌های اولیه دیتابیس به نام‌های Font Awesome
ICON_FIX = {
    'categories': {
        'cat-print': 'print',
        'cat-student': 'graduation-cap',
        'cat-gov': 'building-columns',
        'cat-financial': 'landmark',
    },
    'services': {
        'srv-print-doc': 'print',
        'srv-sanjesh': 'graduation-cap',
        'srv-tamin': 'building-columns',
        'srv-ezdevaj-vam': 'landmark',
    },
}

# فیلد فرم عمومی برای خدمات جدید (ساختار یکسان با خدمات موجود)
GENERIC_FIELDS_JSON = json.dumps([
    {
        'id': 'orderNote',
        'label': 'توضیحات و نکات تکمیلی برای اپراتور',
        'type': 'textarea',
        'placeholder': 'هر نکته‌ای درباره درخواست خود را اینجا بنویسید...',
        'required': False,
        'options': None,
        'helpText': '',
        'defaultValue': None,
        'pricePerUnit': None,
    }
], ensure_ascii=False)

# جدول حروف‌نگاری فارسی به لاتین برای ساخت شناسه‌های پایدار
TRANS = {
    'آ': 'a', 'ا': 'a', 'ب': 'b', 'پ': 'p', 'ت': 't', 'ث': 's', 'ج': 'j',
    'چ': 'ch', 'ح': 'h', 'خ': 'kh', 'د': 'd', 'ذ': 'z', 'ر': 'r', 'ز': 'z',
    'ژ': 'zh', 'س': 's', 'ش': 'sh', 'ص': 's', 'ض': 'z', 'ط': 't', 'ظ': 'z',
    'ع': 'a', 'غ': 'gh', 'ف': 'f', 'ق': 'gh', 'ک': 'k', 'گ': 'g', 'ل': 'l',
    'م': 'm', 'ن': 'n', 'و': 'v', 'ه': 'h', 'ی': 'i', 'ي': 'i', 'ئ': 'i',
    'ء': '', 'أ': 'a', 'إ': 'a', 'ؤ': 'o', 'ة': 'h',
    '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
    '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
}


# ----------------------------------------------------------------------
# توابع کمکی
# ----------------------------------------------------------------------
def slugify(text, max_len=48):
    """تبدیل عنوان فارسی به شناسه لاتین کوتاه و پایدار."""
    s = text.replace('\u200c', '-').replace('\u200f', '').replace('\u200e', '')
    out = []
    for ch in s:
        if ch in TRANS:
            out.append(TRANS[ch])
        elif ch == ' ':
            out.append('-')
        elif ch.isdigit() and ch.isascii():
            out.append(ch)
        elif 'a' <= ch.lower() <= 'z':
            out.append(ch.lower())
    slug = re.sub(r'-+', '-', ''.join(out)).strip('-')
    return slug[:max_len].rstrip('-')


def make_id(prefix, title, used):
    """ساخت شناسه یکتا و پایدار؛ در صورت تکرار، پسوند هش اضافه می‌شود."""
    base = prefix + (slugify(title) or 'item')
    if base not in used:
        used.add(base)
        return base
    suffix = hashlib.md5(title.encode('utf-8')).hexdigest()[:6]
    cand = '{}-{}'.format(base, suffix)
    n = 1
    while cand in used:
        cand = '{}-{}{}'.format(base, suffix, n)
        n += 1
    used.add(cand)
    return cand


def parse_source(path):
    """خواندن دسته‌بندی‌ها و خدمات از فایل cn.md."""
    categories = []
    index_by_title = {}
    current = None
    with open(path, encoding='utf-8') as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            # خطوط توضیح داخل پرانتز (مانند دسته‌های بدون خدمت در تصاویر)
            if line.startswith('*('):
                continue
            # عنوان دسته‌بندی: **دسته: ...** یا **...**
            if line.startswith('**') and line.endswith('**') and len(line) > 4:
                title = line[2:-2].strip()
                if title.startswith('منوی دسته'):  # سرتیتر بخش دوم منو
                    continue
                if title.startswith('دسته:'):
                    title = title[len('دسته:'):].strip()
                if title in index_by_title:
                    current = index_by_title[title]  # ادغام دسته‌های هم‌نام
                else:
                    current = {'title': title, 'services': []}
                    categories.append(current)
                    index_by_title[title] = current
                continue
            # عنوان خدمت: * ...
            if line.startswith('* '):
                if current is None:
                    continue
                current['services'].append(line[2:].strip())
    return categories


def pick_service_icon(title, cat_icon):
    """انتخاب آیکون Font Awesome برای خدمت بر اساس کلیدواژه عنوان."""
    for keyword, icon in SERVICE_ICON_RULES:
        if keyword in title:
            return icon
    return cat_icon or FALLBACK_ICON


# ----------------------------------------------------------------------
# اجرای اصلی
# ----------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description='ایجاد دسته‌بندی‌ها و خدمات کافی‌نت از روی cn.md در دیتابیس SQLite')
    parser.add_argument('--db', default=DEFAULT_DB, help='مسیر فایل دیتابیس')
    parser.add_argument('--source', default=DEFAULT_SOURCE, help='مسیر فایل لیست خدمات (cn.md)')
    parser.add_argument('--price', type=int, default=DEFAULT_BASE_PRICE,
                        help='قیمت پایه خدمات جدید (تومان)')
    parser.add_argument('--time', dest='estimated_time', default=DEFAULT_ESTIMATED_TIME,
                        help='زمان تقریبی انجام خدمات جدید')
    parser.add_argument('--no-backup', action='store_true', help='تهیه نکردن بکاپ')
    parser.add_argument('--no-icon-fix', action='store_true',
                        help='عدم تغییر آیکون ۴ دسته و ۴ خدمت قدیمی')
    parser.add_argument('--dry-run', action='store_true',
                        help='فقط نمایش نتیجه، بدون تغییر دیتابیس')
    args = parser.parse_args()

    if not os.path.exists(args.source):
        print('خطا: فایل لیست خدمات یافت نشد: {}'.format(args.source))
        sys.exit(1)

    categories = parse_source(args.source)
    total_services = sum(len(c['services']) for c in categories)
    print('خوانده شد: {} دسته‌بندی و {} خدمت از "{}"'.format(
        len(categories), total_services, os.path.basename(args.source)))

    # ---------- ساخت ردیف‌ها ----------
    used_cat_ids, used_srv_ids = set(), set()
    cat_rows, srv_rows = [], []
    for c_idx, cat in enumerate(categories, start=1):
        icon, desc = CATEGORY_META.get(cat['title'], (FALLBACK_ICON, None))
        color = PALETTE[(c_idx - 1) % len(PALETTE)]
        cat_id = make_id('cat-', cat['title'], used_cat_ids)
        cat_rows.append((cat_id, cat['title'], icon, desc, color, c_idx))
        for s_idx, srv_title in enumerate(cat['services'], start=1):
            srv_id = make_id('srv-', srv_title, used_srv_ids)
            srv_icon = pick_service_icon(srv_title, icon)
            srv_color = PALETTE[(c_idx * 3 + s_idx) % len(PALETTE)]
            srv_rows.append((
                srv_id, cat_id, srv_title, None,
                args.price, args.estimated_time,
                srv_icon, 1, GENERIC_FIELDS_JSON, '',
                srv_color, s_idx, 0, 0,
            ))

    print()
    print('{:<4} {:<38} {:<24} {:>6}'.format('#', 'شناسه دسته', 'آیکون (Font Awesome)', 'خدمات'))
    print('-' * 78)
    for i, row in enumerate(cat_rows, start=1):
        count = sum(1 for s in srv_rows if s[1] == row[0])
        print('{:<4} {:<38} {:<24} {:>6}'.format(i, row[0], row[2], count))
    print('-' * 78)
    print('جمع کل: {} دسته‌بندی، {} خدمت'.format(len(cat_rows), len(srv_rows)))

    if args.dry_run:
        print()
        print('حالت آزمایشی (--dry-run): هیچ تغییری در دیتابیس اعمال نشد.')
        return

    if not os.path.exists(args.db):
        print('خطا: فایل دیتابیس یافت نشد: {}'.format(args.db))
        sys.exit(1)

    # ---------- پشتیبان‌گیری ----------
    if not args.no_backup:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        backup_path = '{}.backup-{}'.format(args.db, stamp)
        shutil.copy2(args.db, backup_path)
        print()
        print('بکاپ دیتابیس: {}'.format(os.path.basename(backup_path)))

    # ---------- اعمال تغییرات ----------
    conn = sqlite3.connect(args.db)
    conn.isolation_level = None  # مدیریت دستی تراکنش
    cur = conn.cursor()
    try:
        cur.execute('BEGIN')

        cur.executemany('''
            INSERT INTO categories (id, title, icon_name, description, icon_color, sort_order)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title      = excluded.title,
                icon_name  = excluded.icon_name,
                icon_color = excluded.icon_color,
                sort_order = excluded.sort_order
        ''', cat_rows)

        cur.executemany('''
            INSERT INTO services (
                id, category_id, title, description, base_price, estimated_time,
                icon, is_active, fields_json, pricing_formula, icon_color,
                sort_order, is_featured, featured_sort
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                category_id = excluded.category_id,
                title       = excluded.title,
                icon        = excluded.icon,
                icon_color  = excluded.icon_color,
                sort_order  = excluded.sort_order
        ''', srv_rows)

        # اصلاح آیکون ردیف‌های قدیمی به Font Awesome (قیمت‌ها و فرم‌ها دست نمی‌خورند)
        if not args.no_icon_fix:
            for table, mapping in ICON_FIX.items():
                col = 'icon_name' if table == 'categories' else 'icon'
                for row_id, fa_icon in mapping.items():
                    cur.execute(
                        'UPDATE {} SET {} = ? WHERE id = ?'.format(table, col),
                        (fa_icon, row_id))

        # ثبت در جدول seeders (در صورت وجود) برای هماهنگی با بک‌اند
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seeders'")
        if cur.fetchone():
            seeder_name = '003_services_categories_seeder'
            cur.execute('SELECT COUNT(*) FROM seeders WHERE seeder = ?', (seeder_name,))
            if cur.fetchone()[0] == 0:
                ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'
                cur.execute(
                    'INSERT INTO seeders (seeder, ran_at) VALUES (?, ?)',
                    (seeder_name, ts))

        conn.execute('COMMIT')
    except Exception:
        conn.execute('ROLLBACK')
        conn.close()
        raise

    # ---------- اعتبارسنجی ----------
    cur.execute('SELECT COUNT(*) FROM categories')
    n_cats = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM services')
    n_srvs = cur.fetchone()[0]
    cur.execute('''SELECT COUNT(*) FROM services s
                   LEFT JOIN categories c ON c.id = s.category_id
                   WHERE c.id IS NULL''')
    orphans = cur.fetchone()[0]
    conn.close()

    print()
    print('انجام شد.')
    print('مجموع دسته‌بندی‌ها در دیتابیس: {}'.format(n_cats))
    print('مجموع خدمات در دیتابیس: {}'.format(n_srvs))
    print('خدمات بدون دسته‌بندی معتبر: {}'.format(orphans))
    print('قیمت پایه خدمات جدید: {:,} تومان | زمان تقریبی: {}'.format(
        args.price, args.estimated_time))


if __name__ == '__main__':
    main()
