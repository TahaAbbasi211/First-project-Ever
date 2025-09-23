# -*- coding: utf-8 -*-
import os
import io
import csv
import time
import math
import random
import string
import logging
import traceback
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
import telebot
from telebot import types
from telebot.types import Message, CallbackQuery
from telebot.apihelper import ApiException

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text, Index
)
from sqlalchemy.orm import declarative_base, relationship, sessionmaker, scoped_session

# ============================
# Load env
# ============================
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x}
CARD_NUMBER = os.getenv("CARD_NUMBER", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///bot.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set in .env")
if not SUPPORT_USERNAME:
    raise RuntimeError("SUPPORT_USERNAME is not set in .env")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS is not set in .env")
if not CARD_NUMBER:
    raise RuntimeError("CARD_NUMBER is not set in .env")

# ============================
# Logging
# ============================
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("ShopBot")

# ============================
# Database (SQLAlchemy)
# ============================
Base = declarative_base()
engine = create_engine(DATABASE_URL, echo=False, pool_pre_ping=True, future=True)
SessionLocal = scoped_session(sessionmaker(bind=engine, autoflush=False, autocommit=False))

def now_utc():
    return datetime.now(timezone.utc)

def format_price_toman(value: int) -> str:
    s = f"{value:,}".replace(",", "٬")
    return f"{s} تومان"

def rand_code(n=6):
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=n))

class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False)
    value = Column(String(255), nullable=False)

    @staticmethod
    def get(session, key, default=None):
        row = session.query(Setting).filter_by(key=key).first()
        return row.value if row else default

    @staticmethod
    def set(session, key, value):
        row = session.query(Setting).filter_by(key=key).first()
        if not row:
            row = Setting(key=key, value=value)
            session.add(row)
        else:
            row.value = value

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)  # telegram id
    username = Column(String(64))
    first_name = Column(String(128))
    last_name = Column(String(128))
    language_code = Column(String(16))
    created_at = Column(DateTime(timezone=True), default=now_utc)
    last_seen_at = Column(DateTime(timezone=True), default=now_utc)
    allow_broadcast = Column(Boolean, default=True)
    blocked = Column(Boolean, default=False)

    orders = relationship("Order", back_populates="user", lazy="selectin")

Index("idx_users_last_seen", User.last_seen_at)

class VpnProduct(Base):
    __tablename__ = "vpn_products"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)  # e.g. "30 روز - 50 گیگ"
    duration_days = Column(Integer, nullable=False, default=30)
    data_gb = Column(Integer, nullable=True)     # nullable for flexibility
    price_toman = Column(Integer, nullable=False)
    active = Column(Boolean, default=True)

class App(Base):
    __tablename__ = "apps"
    id = Column(Integer, primary_key=True)
    key = Column(String(64), unique=True, nullable=False)  # e.g. spotify
    title = Column(String(255), nullable=False)            # e.g. "خرید اسپاتیفای"
    active = Column(Boolean, default=True)
    plans = relationship("AppPlan", back_populates="app", cascade="all, delete-orphan")

class AppPlan(Base):
    __tablename__ = "app_plans"
    id = Column(Integer, primary_key=True)
    app_id = Column(Integer, ForeignKey("apps.id"), nullable=False)
    title = Column(String(255), nullable=False)    # e.g. "۱ ماهه - 120,000 تومان"
    duration_months = Column(Integer, nullable=True)
    price_toman = Column(Integer, nullable=False)
    active = Column(Boolean, default=True)

    app = relationship("App", back_populates="plans")

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    order_code = Column(String(20), unique=True, nullable=False)  # e.g. ORD-20250923-AB12
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    category = Column(String(16), nullable=False)  # "vpn" | "app"
    item_title = Column(String(255), nullable=False)
    price_toman = Column(Integer, nullable=False)

    vpn_product_id = Column(Integer, ForeignKey("vpn_products.id"), nullable=True)
    app_plan_id = Column(Integer, ForeignKey("app_plans.id"), nullable=True)

    status = Column(String(24), nullable=False, default="awaiting_payment")
    # awaiting_payment -> proof_submitted -> approved -> delivered
    # rejected / cancelled

    payment_proof_file_id = Column(String(255), nullable=True)
    payment_proof_type = Column(String(32), nullable=True)   # photo | document
    approved_by_admin_id = Column(Integer, nullable=True)
    rejected_reason = Column(Text, nullable=True)
    delivery_note = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_utc)
    updated_at = Column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)

    user = relationship("User", back_populates="orders")
    vpn_product = relationship("VpnProduct")
    app_plan = relationship("AppPlan")

Index("idx_orders_user_status", Order.user_id, Order.status)
Index("idx_orders_created", Order.created_at)

class BroadcastLog(Base):
    __tablename__ = "broadcasts"
    id = Column(Integer, primary_key=True)
    admin_id = Column(Integer, nullable=False)
    from_chat_id = Column(Integer, nullable=False)
    message_id = Column(Integer, nullable=False)
    segment = Column(String(32), nullable=False)    # "all" | "active30"
    sent_ok = Column(Integer, default=0)
    sent_fail = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=now_utc)

def init_db_and_seed():
    Base.metadata.create_all(engine)
    s = SessionLocal()
    try:
        # Seed VPN products if empty
        if s.query(VpnProduct).count() == 0:
            seed_vpn = [
                ("30 روز - 50 گیگ", 30, 50, 129000),
                ("30 روز - 80 گیگ", 30, 80, 185000),
                ("30 روز - 100 گیگ", 30, 100, 220000),
                ("90 روز - 150 گیگ", 90, 150, 340000),
                ("90 روز - 200 گیگ", 90, 200, 420000),
                ("90 روز - 250 گیگ", 90, 250, 500000),
                ("90 روز - 300 گیگ", 90, 300, 585000),
                ("تست 1 روز - 1 گیگ", 1, 1, 0),
            ]
            for title, d, g, p in seed_vpn:
                s.add(VpnProduct(title=title, duration_days=d, data_gb=g, price_toman=p))

        # Seed Apps & Plans if empty
        if s.query(App).count() == 0:
            def add_app(key, title, plans):
                app = App(key=key, title=title, active=True)
                s.add(app)
                s.flush()
                for pl_title, months, price in plans:
                    s.add(AppPlan(app_id=app.id, title=pl_title, duration_months=months, price_toman=price, active=True))

            add_app("spotify", "🎵 خرید اسپاتیفای", [
                ("۱ ماهه", 1, 120000),
                ("۳ ماهه", 3, 330000),
                ("۱۲ ماهه", 12, 1200000),
            ])
            add_app("apple_music", "🍎 خرید اپل موزیک", [
                ("۱ ماهه", 1, 150000),
                ("۳ ماهه", 3, 400000),
                ("۱۲ ماهه", 12, 1500000),
            ])
            add_app("apple_tv", "📺 خرید اپل تیوی", [
                ("۱ ماهه", 1, 100000),
                ("۳ ماهه", 3, 270000),
                ("۱۲ ماهه", 12, 950000),
            ])
            add_app("disney", "🏰 خرید دیزنی", [
                ("۱ ماهه", 1, 130000),
                ("۳ ماهه", 3, 350000),
                ("۱۲ ماهه", 12, 1100000),
            ])

        # Seed maintenance=false
        if Setting.get(s, "maintenance", None) is None:
            Setting.set(s, "maintenance", "0")

        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

init_db_and_seed()

# ============================
# Bot
# ============================
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", skip_pending=True)

# --- In-memory admin temp states ---
ADMIN_STATE = {}  # {admin_id: {"mode": "...", "payload": {...}}}

# --- Helpers ---
def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def maintenance_enabled() -> bool:
    s = SessionLocal()
    try:
        val = Setting.get(s, "maintenance", "0")
        return val == "1"
    finally:
        s.close()

def set_maintenance(flag: bool):
    s = SessionLocal()
    try:
        Setting.set(s, "maintenance", "1" if flag else "0")
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

def support_url():
    return f"https://t.me/{SUPPORT_USERNAME}"

def user_tag(u) -> str:
    name = " ".join(x for x in [u.first_name or "", u.last_name or ""] if x).strip() or (f"@{u.username}" if u.username else "بدون‌نام")
    handle = f"@{u.username}" if u.username else f"id:{u.id}"
    return f"{name} ({handle})"

def order_code() -> str:
    return f"ORD-{datetime.utcnow().strftime('%Y%m%d')}-{rand_code(4)}"

def human_status(s: str) -> str:
    m = {
        "awaiting_payment": "در انتظار پرداخت",
        "proof_submitted": "رسید ارسال‌شده",
        "approved": "تأییدشده",
        "delivered": "تحویل‌شده",
        "rejected": "رد شده",
        "cancelled": "لغو شده",
    }
    return m.get(s, s)

# Keyboards
def kb_main():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🛡️ خرید VPN", callback_data="nav:vpn"),
        types.InlineKeyboardButton("🛍️ اشتراک اپلیکیشن‌ها", callback_data="nav:apps"),
        types.InlineKeyboardButton("⚙️ تنظیمات", callback_data="nav:settings"),
        types.InlineKeyboardButton("📞 پشتیبانی", callback_data="nav:support"),
        types.InlineKeyboardButton("🔐 پنل ادمین", callback_data="nav:admin"),
    )
    return kb

def kb_back_main():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="nav:home"))
    return kb

def kb_vpn_menu(session):
    kb = types.InlineKeyboardMarkup(row_width=1)
    products = session.query(VpnProduct).filter_by(active=True).order_by(VpnProduct.duration_days, VpnProduct.data_gb).all()
    for p in products:
        label = f"Vpn - {p.title} - {format_price_toman(p.price_toman)}"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"vpn:{p.id}"))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="nav:home"))
    return kb

def kb_apps_menu(session):
    kb = types.InlineKeyboardMarkup(row_width=1)
    apps = session.query(App).filter_by(active=True).order_by(App.id).all()
    for a in apps:
        kb.add(types.InlineKeyboardButton(a.title, callback_data=f"app:{a.id}"))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="nav:home"))
    return kb

def kb_app_plans(session, app_id: int):
    kb = types.InlineKeyboardMarkup(row_width=1)
    plans = session.query(AppPlan).filter_by(app_id=app_id, active=True).order_by(AppPlan.duration_months).all()
    for pl in plans:
        label = f"{pl.title} - {format_price_toman(pl.price_toman)}"
        kb.add(types.InlineKeyboardButton(label, callback_data=f"plan:{pl.id}"))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="nav:apps"))
    return kb

def kb_payment():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💳 واریز به کارت", callback_data="pay:card"))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="nav:home"))
    return kb

def kb_contact():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("گفتگو در تلگرام (پی‌وی)", url=support_url()))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="nav:home"))
    return kb

def kb_admin_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📣 Broadcast", callback_data="adm:broadcast"),
        types.InlineKeyboardButton("👥 تعداد کاربران", callback_data="adm:users_count"),
        types.InlineKeyboardButton("📤 CSV کاربران", callback_data="adm:export_users"),
        types.InlineKeyboardButton("📦 CSV سفارش‌ها", callback_data="adm:export_orders"),
    )
    kb.add(
        types.InlineKeyboardButton("🛠 حالت تعمیرات", callback_data="adm:maintenance"),
        types.InlineKeyboardButton("📊 آمار", callback_data="adm:stats"),
        types.InlineKeyboardButton("🛒 مدیریت VPN", callback_data="adm:mg_vpn"),
        types.InlineKeyboardButton("🛍 مدیریت اپ‌ها", callback_data="adm:mg_apps"),
    )
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="nav:home"))
    return kb

def kb_broadcast_confirm():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ ارسال به همه", callback_data="adm:bcast_send:all"),
        types.InlineKeyboardButton("🟢 ارسال به فعال‌های ۳۰ روز", callback_data="adm:bcast_send:active30"),
    )
    kb.add(types.InlineKeyboardButton("❌ انصراف", callback_data="adm:bcast_cancel"))
    return kb

def kb_approve_reject(order_id: int):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ تأیید", callback_data=f"adm:approve:{order_id}"),
        types.InlineKeyboardButton("❌ رد", callback_data=f"adm:reject:{order_id}")
    )
    return kb

def kb_user_settings(user):
    kb = types.InlineKeyboardMarkup(row_width=1)
    label = "🔔 دریافت پیام‌های همگانی: روشن" if user.allow_broadcast else "🔕 دریافت پیام‌های همگانی: خاموش"
    kb.add(types.InlineKeyboardButton(label, callback_data="usr:toggle_bcast"))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="nav:home"))
    return kb

# ============================
# Utilities
# ============================
def touch_user(message: Message):
    s = SessionLocal()
    try:
        u = s.get(User, message.from_user.id)
        if not u:
            u = User(
                id=message.from_user.id,
                username=message.from_user.username,
                first_name=message.from_user.first_name,
                last_name=message.from_user.last_name,
                language_code=message.from_user.language_code,
                created_at=now_utc(),
                last_seen_at=now_utc()
            )
            s.add(u)
        else:
            u.username = message.from_user.username
            u.first_name = message.from_user.first_name
            u.last_name = message.from_user.last_name
            u.language_code = message.from_user.language_code
            u.last_seen_at = now_utc()
        s.commit()
        return u
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

def guard_maintenance(call_or_msg):
    """Return True if interaction must be blocked due to maintenance (unless admin)."""
    uid = (call_or_msg.from_user.id if isinstance(call_or_msg, (Message, CallbackQuery)) else None)
    if uid and is_admin(uid):
        return False
    return maintenance_enabled()

def ensure_order_for_proof(user_id: int):
    """Fetch latest awaiting_payment order to attach proof."""
    s = SessionLocal()
    try:
        o = s.query(Order).filter_by(user_id=user_id, status="awaiting_payment").order_by(Order.created_at.desc()).first()
        return (s, o)  # caller must close/commit
    except Exception:
        s.close()
        raise

# ============================
# Command Handlers
# ============================
@bot.message_handler(commands=["start"])
def cmd_start(message: Message):
    user = touch_user(message)
    if guard_maintenance(message):
        bot.send_message(message.chat.id, "🛠 ربات در حال تعمیرات است. لطفاً بعداً امتحان کنید.\nاگر ضروری است از پشتیبانی کمک بگیرید.", reply_markup=kb_contact())
        return
    bot.send_message(message.chat.id, "سلام 👋\nاز منو یکی را انتخاب کنید:", reply_markup=kb_main())

@bot.message_handler(commands=["id"])
def cmd_id(message: Message):
    touch_user(message)
    bot.reply_to(message, f"🆔 ID شما: <code>{message.from_user.id}</code>")

# ============================
# Callback Handlers (Navigation & Actions)
# ============================
@bot.callback_query_handler(func=lambda c: True)
def on_callback(call: CallbackQuery):
    try:
        data = call.data or ""
        uid = call.from_user.id

        # Maintenance gate (except some items & admins)
        if not data.startswith("nav:") and not is_admin(uid) and maintenance_enabled():
            bot.answer_callback_query(call.id, "ربات در حال تعمیرات است.", show_alert=True)
            return

        bot.answer_callback_query(call.id)

        if data == "nav:home":
            bot.edit_message_text("از منو یکی را انتخاب کنید:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb_main()); return

        if data == "nav:vpn":
            s = SessionLocal()
            try:
                bot.edit_message_text("🛡️ خرید VPN — پلن مورد نظر را انتخاب کنید:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb_vpn_menu(s))
            finally:
                s.close()
            return

        if data.startswith("vpn:"):
            vpn_id = int(data.split(":")[1])
            s = SessionLocal()
            try:
                p = s.get(VpnProduct, vpn_id)
                if not p or not p.active:
                    bot.answer_callback_query(call.id, "این محصول موجود نیست.", show_alert=True); return
                o = Order(
                    order_code=order_code(),
                    user_id=uid,
                    category="vpn",
                    item_title=f"VPN — {p.title}",
                    price_toman=p.price_toman,
                    vpn_product_id=p.id,
                    status="awaiting_payment",
                )
                s.add(o); s.commit()
                bot.edit_message_text(
                    f"✅ «{o.item_title}» انتخاب شد.\n"
                    f"کد سفارش: <code>{o.order_code}</code>\n\n"
                    f"برای ادامه پرداخت:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=kb_payment()
                )
            except Exception:
                s.rollback(); raise
            finally:
                s.close()
            return

        if data == "nav:apps":
            s = SessionLocal()
            try:
                bot.edit_message_text("🛍️ اشتراک اپ‌ها — یک اپ را انتخاب کنید:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb_apps_menu(s))
            finally:
                s.close()
            return

        if data.startswith("app:"):
            app_id = int(data.split(":")[1])
            s = SessionLocal()
            try:
                a = s.get(App, app_id)
                if not a or not a.active:
                    bot.answer_callback_query(call.id, "این اپ فعال نیست.", show_alert=True); return
                bot.edit_message_text(f"{a.title}\nیک پلن را انتخاب کنید:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb_app_plans(s, a.id))
            finally:
                s.close()
            return

        if data.startswith("plan:"):
            plan_id = int(data.split(":")[1])
            s = SessionLocal()
            try:
                pl = s.get(AppPlan, plan_id)
                if not pl or not pl.active:
                    bot.answer_callback_query(call.id, "این پلن فعال نیست.", show_alert=True); return
                o = Order(
                    order_code=order_code(),
                    user_id=uid,
                    category="app",
                    item_title=f"{pl.app.title} — {pl.title}",
                    price_toman=pl.price_toman,
                    app_plan_id=pl.id,
                    status="awaiting_payment",
                )
                s.add(o); s.commit()
                bot.edit_message_text(
                    f"✅ {o.item_title}\n"
                    f"کد سفارش: <code>{o.order_code}</code>\n\n"
                    f"برای ادامه پرداخت:",
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    reply_markup=kb_payment()
                )
            except Exception:
                s.rollback(); raise
            finally:
                s.close()
            return

        if data == "pay:card":
            msg = (f"💳 شماره کارت برای واریز:\n<code>{CARD_NUMBER}</code>\n\n"
                   "✅ پس از پرداخت، لطفاً اسکرین‌شات/رسید تراکنش را <b>همینجا</b> ارسال کنید.\n"
                   "ℹ️ حتماً کد سفارش درج‌شده در گفت‌وگو را نزد خود نگه دارید.")
            bot.send_message(call.message.chat.id, msg)
            return

        if data == "nav:support":
            bot.edit_message_text("📞 تماس با پشتیبانی\nبرای گفتگو مستقیم با پشتیبان، روی دکمه زیر بزنید:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb_contact()); return

        if data == "nav:settings":
            s = SessionLocal()
            try:
                u = s.get(User, uid)
                bot.edit_message_text("⚙️ تنظیمات حساب:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb_user_settings(u))
            finally:
                s.close()
            return

        if data == "usr:toggle_bcast":
            s = SessionLocal()
            try:
                u = s.get(User, uid)
                u.allow_broadcast = not u.allow_broadcast
                s.commit()
                bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=kb_user_settings(u))
                bot.answer_callback_query(call.id, "تنظیم شد.", show_alert=False)
            except Exception:
                s.rollback(); raise
            finally:
                s.close()
            return

        # Admin panel
        if data == "nav:admin":
            if not is_admin(uid):
                bot.answer_callback_query(call.id, "دسترسی ندارید.", show_alert=True); return
            bot.edit_message_text("🔐 پنل ادمین — یک گزینه را انتخاب کنید:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=kb_admin_menu()); return

        if data.startswith("adm:"):
            if not is_admin(uid):
                bot.answer_callback_query(call.id, "دسترسی ندارید.", show_alert=True); return
            action = data.split(":")[1]

            s = SessionLocal()
            try:
                if action == "users_count":
                    total = s.query(User).count()
                    active30 = s.query(User).filter(User.last_seen_at >= now_utc() - timedelta(days=30)).count()
                    bot.send_message(call.message.chat.id, f"👥 تعداد کاربران: {total}\n🟢 فعال ۳۰ روز اخیر: {active30}")
                    return

                if action == "export_users":
                    buf = io.StringIO(); w = csv.writer(buf)
                    w.writerow(["user_id","username","first_name","last_name","allow_broadcast","blocked","created_at","last_seen_at"])
                    for u in s.query(User).order_by(User.id).all():
                        w.writerow([u.id, u.username or "", u.first_name or "", u.last_name or "", int(u.allow_broadcast), int(u.blocked), u.created_at, u.last_seen_at])
                    datafile = io.BytesIO(buf.getvalue().encode("utf-8")); datafile.name = "users.csv"
                    bot.send_document(call.message.chat.id, datafile, caption="📤 خروجی کاربران")
                    return

                if action == "export_orders":
                    buf = io.StringIO(); w = csv.writer(buf)
                    w.writerow(["order_id","order_code","user_id","category","item_title","price_toman","status","approved_by","created_at","updated_at"])
                    for o in s.query(Order).order_by(Order.created_at.desc()).all():
                        w.writerow([o.id, o.order_code, o.user_id, o.category, o.item_title, o.price_toman, o.status, o.approved_by_admin_id or "", o.created_at, o.updated_at])
                    datafile = io.BytesIO(buf.getvalue().encode("utf-8")); datafile.name = "orders.csv"
                    bot.send_document(call.message.chat.id, datafile, caption="📤 خروجی سفارش‌ها")
                    return

                if action == "maintenance":
                    cur = maintenance_enabled()
                    set_maintenance(not cur)
                    status = "فعال شد ✅" if not cur else "غیرفعال شد ❌"
                    bot.send_message(call.message.chat.id, f"🛠 حالت تعمیرات {status}")
                    return

                if action == "stats":
                    today = now_utc().date()
                    start_today = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
                    start_7d = now_utc() - timedelta(days=7)
                    start_month = datetime(now_utc().year, now_utc().month, 1, tzinfo=timezone.utc)

                    def agg(start):
                        q = s.query(Order).filter(Order.created_at >= start)
                        total = q.count()
                        approved = q.filter(Order.status.in_(["approved","delivered"])).count()
                        delivered = q.filter(Order.status == "delivered").count()
                        income = sum(x.price_toman for x in q.filter(Order.status.in_(["approved","delivered"])).all())
                        return total, approved, delivered, income

                    t_total, t_approved, t_delivered, t_income = agg(start_today)
                    w_total, w_appr, w_deliv, w_income = agg(start_7d)
                    m_total, m_appr, m_deliv, m_income = agg(start_month)

                    msg = (f"📊 آمار\n\n"
                           f"📅 امروز:\n"
                           f"• کل سفارش‌ها: {t_total}\n"
                           f"• تأیید/تحویل: {t_approved}/{t_delivered}\n"
                           f"• درآمد: {format_price_toman(t_income)}\n\n"
                           f"🗓 ۷ روز اخیر:\n"
                           f"• کل: {w_total} | تأیید: {w_appr} | تحویل: {w_deliv}\n"
                           f"• درآمد: {format_price_toman(w_income)}\n\n"
                           f"🗓 ماه جاری:\n"
                           f"• کل: {m_total} | تأیید: {m_appr} | تحویل: {m_deliv}\n"
                           f"• درآمد: {format_price_toman(m_income)}")
                    bot.send_message(call.message.chat.id, msg)
                    return

                if action == "mg_vpn":
                    products = s.query(VpnProduct).order_by(VpnProduct.id).all()
                    lines = ["🛒 محصولات VPN:"]
                    for p in products:
                        lines.append(f"• #{p.id} — {p.title} — {format_price_toman(p.price_toman)} — {'✅' if p.active else '❌'}")
                    lines.append("\n➕ برای افزودن/ویرایش، دستور زیر را بفرستید:")
                    lines.append("<code>/add_vpn عنوان | روز | گیگ | قیمت_تومان</code>")
                    lines.append("<code>/edit_vpn ID | عنوان | روز | گیگ | قیمت_تومان | active(0/1)</code>")
                    lines.append("<code>/del_vpn ID</code>")
                    bot.send_message(call.message.chat.id, "\n".join(lines))
                    return

                if action == "mg_apps":
                    apps = s.query(App).order_by(App.id).all()
                    lines = ["🛍 اپ‌ها و پلن‌ها:"]
                    for a in apps:
                        lines.append(f"• #{a.id} — {a.title} ({a.key}) — {'✅' if a.active else '❌'}")
                        for pl in a.plans:
                            lines.append(f"   └ plan #{pl.id} — {pl.title} — {pl.duration_months or '-'} ماه — {format_price_toman(pl.price_toman)} — {'✅' if pl.active else '❌'}")
                    lines += [
                        "\n➕ مدیریت اپ/پلن با دستورات:",
                        "<code>/add_app key | عنوان</code>",
                        "<code>/edit_app ID | key | عنوان | active(0/1)</code>",
                        "<code>/del_app ID</code>",
                        "<code>/add_plan app_id | عنوان | ماه | قیمت_تومان</code>",
                        "<code>/edit_plan ID | عنوان | ماه | قیمت_تومان | active(0/1)</code>",
                        "<code>/del_plan ID</code>",
                    ]
                    bot.send_message(call.message.chat.id, "\n".join(lines))
                    return

                if action == "broadcast":
                    ADMIN_STATE[uid] = {"mode": "await_broadcast_draft"}
                    bot.send_message(call.message.chat.id, "📣 لطفاً محتوای پیام همگانی را ارسال کنید (متن/عکس/ویدیو/سند...). سپس گزینهٔ ارسال را می‌بینید.")
                    return

                if action == "bcast_send":
                    bot.answer_callback_query(call.id, "از دکمهٔ مربوط به سگمنت استفاده کنید.", show_alert=True)
                    return

                # Approve / Reject / Broadcast confirm with segment
                if action == "approve":
                    order_id = int(data.split(":")[2])
                    o = s.get(Order, order_id)
                    if not o:
                        bot.answer_callback_query(call.id, "سفارش یافت نشد.", show_alert=True); return
                    o.status = "approved"
                    o.approved_by_admin_id = uid
                    s.commit()

                    # Prompt admin for delivery message
                    ADMIN_STATE[uid] = {"mode": "await_delivery", "order_id": o.id}
                    bot.send_message(call.message.chat.id, f"✅ سفارش {o.order_code} تأیید شد.\nلطفاً پیام «تحویل» را ارسال کنید تا برای کاربر ارسال شود (می‌تواند متن/فایل باشد).")
                    # Notify user
                    bot.send_message(o.user_id, f"✅ رسید پرداخت شما برای سفارش <code>{o.order_code}</code> تأیید شد.\nبه‌زودی اطلاعات سرویس برای شما ارسال می‌شود.")
                    return

                if action == "reject":
                    order_id = int(data.split(":")[2])
                    o = s.get(Order, order_id)
                    if not o:
                        bot.answer_callback_query(call.id, "سفارش یافت نشد.", show_alert=True); return
                    o.status = "rejected"
                    o.approved_by_admin_id = uid
                    s.commit()
                    ADMIN_STATE[uid] = {"mode": "await_reject_reason", "order_id": o.id}
                    bot.send_message(call.message.chat.id, f"❌ سفارش {o.order_code} رد شد.\nلطفاً دلیل رد را ارسال کنید تا برای کاربر نمایش داده شود.")
                    return

                if action == "bcast_send":
                    # not used — legacy
                    return

            finally:
                s.close()

        # Broadcast confirm with segment: adm:bcast_send:all or :active30
        if data.startswith("adm:bcast_send:"):
            if not is_admin(uid):
                bot.answer_callback_query(call.id, "دسترسی ندارید.", show_alert=True); return

            st = ADMIN_STATE.get(uid)
            if not st or st.get("mode") != "broadcast_ready":
                bot.answer_callback_query(call.id, "پیش‌نویسی وجود ندارد.", show_alert=True); return

            segment = data.split(":")[2]
            draft = st["draft"]  # {"from_chat_id": int, "message_id": int}
            from_chat_id = draft["from_chat_id"]
            message_id = draft["message_id"]

            s = SessionLocal()
            try:
                if segment == "all":
                    target_q = s.query(User).filter(User.allow_broadcast == True)
                elif segment == "active30":
                    target_q = s.query(User).filter(User.allow_broadcast == True, User.last_seen_at >= now_utc() - timedelta(days=30))
                else:
                    bot.answer_callback_query(call.id, "سگمنت نامعتبر.", show_alert=True); return

                targets = [u.id for u in target_q.all()]
            finally:
                s.close()

            sent_ok, sent_fail = broadcast_copy(draft, targets)
            # log in db
            s = SessionLocal()
            try:
                s.add(BroadcastLog(admin_id=uid, from_chat_id=from_chat_id, message_id=message_id, segment=segment, sent_ok=sent_ok, sent_fail=sent_fail))
                s.commit()
            finally:
                s.close()

            ADMIN_STATE.pop(uid, None)
            bot.edit_message_text(f"✅ ارسال همگانی پایان یافت.\nموفق: {sent_ok}\nناموفق: {sent_fail}", chat_id=call.message.chat.id, message_id=call.message.message_id)
            return

        if data == "adm:broadcast_cancel":
            if not is_admin(uid):
                bot.answer_callback_query(call.id, "دسترسی ندارید.", show_alert=True); return
            ADMIN_STATE.pop(uid, None)
            bot.edit_message_text("❌ ارسال همگانی لغو شد.", chat_id=call.message.chat.id, message_id=call.message.message_id)
            return

    except Exception as e:
        log.error("Callback error: %s", traceback.format_exc())
        try:
            bot.answer_callback_query(call.id, "خطا رخ داد.", show_alert=True)
        except Exception:
            pass

# ============================
# Payment proof (single handler)
# ============================
@bot.message_handler(content_types=["photo", "document"])
def on_payment_proof(message: Message):
    user = touch_user(message)

    if guard_maintenance(message):
        bot.reply_to(message, "🛠 ربات در حال تعمیرات است.")
        return

    s, order = ensure_order_for_proof(user.id)
    try:
        if not order:
            # No awaiting order
            return

        # Attach proof
        if message.content_type == "photo":
            file_id = message.photo[-1].file_id
            ptype = "photo"
        else:
            file_id = message.document.file_id
            ptype = "document"

        order.payment_proof_file_id = file_id
        order.payment_proof_type = ptype
        order.status = "proof_submitted"
        s.commit()

        caption = (f"🧾 رسید پرداخت جدید\n"
                   f"کاربر: {user_tag(message.from_user)}\n"
                   f"کد سفارش: <code>{order.order_code}</code>\n"
                   f"سفارش: {order.item_title}\n"
                   f"قیمت: {format_price_toman(order.price_toman)}\n"
                   f"شناسه چت: <code>{message.chat.id}</code>\n"
                   f"زمان: {message.date}")

        # Send to admins
        for admin_id in ADMIN_IDS:
            try:
                if ptype == "photo":
                    bot.send_photo(admin_id, file_id, caption=caption, reply_markup=kb_approve_reject(order.id))
                else:
                    bot.send_document(admin_id, file_id, caption=caption, reply_markup=kb_approve_reject(order.id))
            except Exception:
                pass

        bot.reply_to(message, "✅ رسید پرداخت دریافت شد. پشتیبانی بررسی خواهد کرد.")

    except Exception:
        s.rollback()
        log.error("Proof handler error: %s", traceback.format_exc())
        bot.reply_to(message, "⚠️ خطایی رخ داد. لطفاً دوباره تلاش کنید.")
    finally:
        s.close()

# ============================
# Admin text commands for products / delivery / reject reason / broadcast draft
# ============================
def parse_parts(text, sep="|", expected=None):
    parts = [p.strip() for p in text.split(sep)]
    if expected and len(parts) < expected:
        raise ValueError("پارامترها ناکافی است.")
    return parts

@bot.message_handler(commands=["add_vpn"])
def add_vpn(message: Message):
    if not is_admin(message.from_user.id): return
    try:
        _, payload = message.text.split(" ", 1)
        title, days, gb, price = parse_parts(payload, expected=4)
        s = SessionLocal()
        try:
            s.add(VpnProduct(title=title, duration_days=int(days), data_gb=int(gb), price_toman=int(price), active=True))
            s.commit()
            bot.reply_to(message, "✅ VPN اضافه شد.")
        finally:
            s.close()
    except Exception as e:
        bot.reply_to(message, "❌ فرمت: <code>/add_vpn عنوان | روز | گیگ | قیمت_تومان</code>")

@bot.message_handler(commands=["edit_vpn"])
def edit_vpn(message: Message):
    if not is_admin(message.from_user.id): return
    try:
        _, payload = message.text.split(" ", 1)
        id_str, title, days, gb, price, active = parse_parts(payload, expected=6)
        s = SessionLocal()
        try:
            p = s.get(VpnProduct, int(id_str))
            if not p: bot.reply_to(message, "یافت نشد."); return
            p.title = title; p.duration_days = int(days); p.data_gb = int(gb); p.price_toman = int(price); p.active = bool(int(active))
            s.commit()
            bot.reply_to(message, "✅ VPN ویرایش شد.")
        finally:
            s.close()
    except Exception:
        bot.reply_to(message, "❌ فرمت: <code>/edit_vpn ID | عنوان | روز | گیگ | قیمت_تومان | active(0/1)</code>")

@bot.message_handler(commands=["del_vpn"])
def del_vpn(message: Message):
    if not is_admin(message.from_user.id): return
    try:
        _, id_str = message.text.split(" ", 1)
        s = SessionLocal()
        try:
            p = s.get(VpnProduct, int(id_str))
            if not p: bot.reply_to(message, "یافت نشد."); return
            s.delete(p); s.commit()
            bot.reply_to(message, "🗑 حذف شد.")
        finally:
            s.close()
    except Exception:
        bot.reply_to(message, "❌ فرمت: <code>/del_vpn ID</code>")

@bot.message_handler(commands=["add_app"])
def add_app(message: Message):
    if not is_admin(message.from_user.id): return
    try:
        _, payload = message.text.split(" ", 1)
        key, title = parse_parts(payload, expected=2)
        s = SessionLocal()
        try:
            s.add(App(key=key, title=title, active=True))
            s.commit()
            bot.reply_to(message, "✅ اپ اضافه شد.")
        finally:
            s.close()
    except Exception:
        bot.reply_to(message, "❌ فرمت: <code>/add_app key | عنوان</code>")

@bot.message_handler(commands=["edit_app"])
def edit_app(message: Message):
    if not is_admin(message.from_user.id): return
    try:
        _, payload = message.text.split(" ", 1)
        id_str, key, title, active = parse_parts(payload, expected=4)
        s = SessionLocal()
        try:
            a = s.get(App, int(id_str))
            if not a: bot.reply_to(message, "یافت نشد."); return
            a.key = key; a.title = title; a.active = bool(int(active))
            s.commit(); bot.reply_to(message, "✅ اپ ویرایش شد.")
        finally:
            s.close()
    except Exception:
        bot.reply_to(message, "❌ فرمت: <code>/edit_app ID | key | عنوان | active(0/1)</code>")

@bot.message_handler(commands=["del_app"])
def del_app(message: Message):
    if not is_admin(message.from_user.id): return
    try:
        _, id_str = message.text.split(" ", 1)
        s = SessionLocal()
        try:
            a = s.get(App, int(id_str))
            if not a: bot.reply_to(message, "یافت نشد."); return
            s.delete(a); s.commit()
            bot.reply_to(message, "🗑 حذف شد.")
        finally:
            s.close()
    except Exception:
        bot.reply_to(message, "❌ فرمت: <code>/del_app ID</code>")

@bot.message_handler(commands=["add_plan"])
def add_plan(message: Message):
    if not is_admin(message.from_user.id): return
    try:
        _, payload = message.text.split(" ", 1)
        app_id, title, months, price = parse_parts(payload, expected=4)
        s = SessionLocal()
        try:
            a = s.get(App, int(app_id))
            if not a: bot.reply_to(message, "اپ یافت نشد."); return
            s.add(AppPlan(app_id=a.id, title=title, duration_months=int(months), price_toman=int(price), active=True))
            s.commit(); bot.reply_to(message, "✅ پلن اضافه شد.")
        finally:
            s.close()
    except Exception:
        bot.reply_to(message, "❌ فرمت: <code>/add_plan app_id | عنوان | ماه | قیمت_تومان</code>")

@bot.message_handler(commands=["edit_plan"])
def edit_plan(message: Message):
    if not is_admin(message.from_user.id): return
    try:
        _, payload = message.text.split(" ", 1)
        id_str, title, months, price, active = parse_parts(payload, expected=5)
        s = SessionLocal()
        try:
            pl = s.get(AppPlan, int(id_str))
            if not pl: bot.reply_to(message, "پلن یافت نشد."); return
            pl.title = title; pl.duration_months = int(months); pl.price_toman = int(price); pl.active = bool(int(active))
            s.commit(); bot.reply_to(message, "✅ پلن ویرایش شد.")
        finally:
            s.close()
    except Exception:
        bot.reply_to(message, "❌ فرمت: <code>/edit_plan ID | عنوان | ماه | قیمت_تومان | active(0/1)</code>")

@bot.message_handler(commands=["del_plan"])
def del_plan(message: Message):
    if not is_admin(message.from_user.id): return
    try:
        _, id_str = message.text.split(" ", 1)
        s = SessionLocal()
        try:
            pl = s.get(AppPlan, int(id_str))
            if not pl: bot.reply_to(message, "یافت نشد."); return
            s.delete(pl); s.commit()
            bot.reply_to(message, "🗑 حذف شد.")
        finally:
            s.close()
    except Exception:
        bot.reply_to(message, "❌ فرمت: <code>/del_plan ID</code>")

# --- Admin free-text states: delivery / reject reason / broadcast draft ---
@bot.message_handler(content_types=[
    "text","photo","video","animation","document","audio","voice","video_note"
])
def admin_state_catcher(message: Message):
    uid = message.from_user.id
    touch_user(message)

    # Non-admins: ignore states
    if uid not in ADMIN_IDS:
        return

    st = ADMIN_STATE.get(uid)
    if not st:
        return

    mode = st.get("mode")

    # Broadcast draft capture
    if mode == "await_broadcast_draft":
        # Save draft
        ADMIN_STATE[uid] = {
            "mode": "broadcast_ready",
            "draft": {"from_chat_id": message.chat.id, "message_id": message.message_id}
        }
        bot.reply_to(message, "پیش‌نویس ذخیره شد. سگمنت ارسال را انتخاب کنید:", reply_markup=kb_broadcast_confirm())
        return

    # Delivery content for approved order
    if mode == "await_delivery":
        order_id = st.get("order_id")
        s = SessionLocal()
        try:
            o = s.get(Order, order_id)
            if not o:
                bot.reply_to(message, "سفارش یافت نشد.")
                ADMIN_STATE.pop(uid, None)
                return

            # Copy admin message to user
            try:
                bot.copy_message(chat_id=o.user_id, from_chat_id=message.chat.id, message_id=message.message_id)
            except Exception as e:
                log.warning("Copy to user failed: %s", e)

            o.status = "delivered"
            o.delivery_note = f"delivered_by_admin:{uid} at {now_utc().isoformat()}"
            s.commit()
            bot.reply_to(message, f"✅ پیام تحویل برای کاربر {o.user_id} ارسال شد.")
        except Exception:
            s.rollback(); raise
        finally:
            s.close()

        ADMIN_STATE.pop(uid, None)
        return

    # Reject reason to send to user
    if mode == "await_reject_reason":
        order_id = st.get("order_id")
        s = SessionLocal()
        try:
            o = s.get(Order, order_id)
            if not o:
                bot.reply_to(message, "سفارش یافت نشد.")
                ADMIN_STATE.pop(uid, None)
                return
            o.rejected_reason = message.text if message.content_type == "text" else "(بدون توضیح متنی)"
            s.commit()
            try:
                bot.send_message(o.user_id, f"❌ سفارش <code>{o.order_code}</code> رد شد.\nدلیل: {o.rejected_reason}\nدر صورت نیاز با پشتیبانی در ارتباط باشید: {support_url()}")
            except Exception:
                pass
            bot.reply_to(message, "✅ دلیل برای کاربر ارسال شد.")
        except Exception:
            s.rollback(); raise
        finally:
            s.close()

        ADMIN_STATE.pop(uid, None)
        return

# ============================
# Broadcast sender with backoff & block detection
# ============================
def broadcast_copy(draft, user_ids):
    from_chat_id = draft["from_chat_id"]
    message_id = draft["message_id"]

    sent_ok = 0
    sent_fail = 0
    batch = 0

    s = SessionLocal()
    try:
        for uid in user_ids:
            batch += 1
            try:
                bot.copy_message(chat_id=uid, from_chat_id=from_chat_id, message_id=message_id)
                sent_ok += 1
            except ApiException as e:
                sent_fail += 1
                # if blocked, mark
                if "Forbidden: bot was blocked by the user" in str(e) or "user is deactivated" in str(e):
                    u = s.get(User, uid)
                    if u:
                        u.blocked = True
                        u.allow_broadcast = False
                        s.add(u)
                        s.commit()
                # handle flood control
                elif "Too Many Requests" in str(e):
                    # naive backoff read retry-after if exists in e.result_json
                    retry_after = 1
                    try:
                        retry_after = int(getattr(e, "result_json", {}).get("parameters", {}).get("retry_after", 1))
                    except Exception:
                        pass
                    time.sleep(retry_after + 1)
                else:
                    # generic small sleep to be gentle
                    time.sleep(0.05)
            except Exception:
                sent_fail += 1

            # pace messages
            time.sleep(0.03)

            # soft break per 50 messages
            if batch % 50 == 0:
                time.sleep(0.5)
    finally:
        s.close()

    return sent_ok, sent_fail

# ============================
# Run
# ============================
if __name__ == "__main__":
    log.info("Bot is running…")
    # توصیه تولیدی: از وبهوک استفاده کنید. اینجا برای سادگی polling:
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=30, allowed_updates=telebot.util.update_types)
        except Exception as e:
            log.error("Polling crashed: %s", traceback.format_exc())
            time.sleep(3)
