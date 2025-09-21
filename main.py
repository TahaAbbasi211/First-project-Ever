# -*- coding: utf-8 -*-
import io
import csv
import time
import telebot
from telebot import types
from telebot.types import Message, CallbackQuery

API_TOKEN = "8241999443:AAGPnd_ETBcb31HqJ6HmnJ4HvUqFgApT9NA"  # ← replace with a fresh token
SUPPORT_USERNAME = "CallMeTaha"     # بدون @
ADMIN_IDS = {5585660160}            # عددی
CARD_NUMBER = "62198961966525049"

bot = telebot.TeleBot(API_TOKEN, parse_mode="HTML")

# ================= VPN PRODUCTS =================
VPN_PRODUCTS = [
    "Vpn - 30 روز - 50 گیگ - 129,000 تومان",
    "Vpn - 30 روز - 80 گیگ - 185,000 تومان",
    "Vpn - 30 روز - 100 گیگ - 220,000 تومان",
    "Vpn - 90 روز - 150 گیگ - 340,000 تومان",
    "Vpn - 90 روز - 200 گیگ - 420,000 تومان",
    "Vpn - 90 روز - 250 گیگ - 500,000 تومان",
    "Vpn - 90 روز - 300 گیگ - 585,000 تومان",
    "Vpn test - 1 روز - 1 گیگ - 0 تومان",
]

# ================= APP SUBSCRIPTIONS =================
APPS = {
    "spotify": {"title": "🎵 خرید اسپاتیفای", "plans": ["۱ ماهه - 120,000 تومان", "۳ ماهه - 330,000 تومان", "۱۲ ماهه - 1,200,000 تومان"]},
    "apple_music": {"title": "🍎 خرید اپل موزیک", "plans": ["۱ ماهه - 150,000 تومان", "۳ ماهه - 400,000 تومان", "۱۲ ماهه - 1,500,000 تومان"]},
    "apple_tv": {"title": "📺 خرید اپل تیوی", "plans": ["۱ ماهه - 100,000 تومان", "۳ ماهه - 270,000 تومان", "۱۲ ماهه - 950,000 تومان"]},
    "disney": {"title": "🏰 خرید دیزنی", "plans": ["۱ ماهه - 130,000 تومان", "۳ ماهه - 350,000 تومان", "۱۲ ماهه - 1,100,000 تومان"]},
}

# ================= STATE =================
USERS = set()
MAINTENANCE = {"enabled": False}
PENDING_PAYMENT = {}  # {user_id: {"category": "vpn"|"app", "item": "title/text"}}

# --- NEW: Broadcast state (per-admin) ---
# Keeps the draft message location to copy from later
BROADCAST_DRAFT = {}   # {admin_id: {"from_chat_id": int, "message_id": int}}
BROADCAST_AWAIT = set()  # set of admin_ids who are expected to send a draft next

# ================= KEYBOARDS =================
def main_menu_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(
        types.InlineKeyboardButton("🛡️ خرید VPN", callback_data="buy_vpn"),
        types.InlineKeyboardButton("🛍️ خرید اشتراک اپلیکیشن‌ها", callback_data="buy_apps"),
        types.InlineKeyboardButton("📞 تماس با پشتیبانی", callback_data="contact_us"),
        types.InlineKeyboardButton("🔐 ورود به پنل ادمین", callback_data="admin_login"),
    )
    return kb

def vpn_menu_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, product in enumerate(VPN_PRODUCTS):
        kb.add(types.InlineKeyboardButton(product, callback_data=f"vpn_plan:{idx}"))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="back_main"))
    return kb

def apps_menu_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    for key, app in APPS.items():
        kb.add(types.InlineKeyboardButton(app["title"], callback_data=f"app:{key}"))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="back_main"))
    return kb

def app_plans_keyboard(app_key):
    kb = types.InlineKeyboardMarkup(row_width=1)
    for idx, plan in enumerate(APPS[app_key]["plans"]):
        kb.add(types.InlineKeyboardButton(plan, callback_data=f"app_plan:{app_key}:{idx}"))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="buy_apps"))
    return kb

def payment_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💳 واریز به کارت", callback_data="pay_card"))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="back_main"))
    return kb

def contact_keyboard():
    url = f"https://t.me/{SUPPORT_USERNAME}"
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("گفتگو در تلگرام (پیوی)", url=url))
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="back_main"))
    return kb

def admin_menu_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📣 Broadcast", callback_data="admin:broadcast"),
        types.InlineKeyboardButton("👥 تعداد کاربران", callback_data="admin:users_count"),
        types.InlineKeyboardButton("📤 خروجی CSV", callback_data="admin:export_users"),
        types.InlineKeyboardButton("🛠 حالت تعمیرات", callback_data="admin:maintenance"),
        types.InlineKeyboardButton("🛒 محصولات VPN", callback_data="admin:list_vpn"),
        types.InlineKeyboardButton("🛍 اشتراک اپ‌ها", callback_data="admin:list_apps"),
    )
    kb.add(types.InlineKeyboardButton("⬅️ بازگشت", callback_data="back_main"))
    return kb

# --- NEW: Broadcast confirm keyboard ---
def broadcast_confirm_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ ارسال به همه", callback_data="admin:broadcast_send"),
        types.InlineKeyboardButton("❌ انصراف", callback_data="admin:broadcast_cancel"),
    )
    return kb

# ================= HELPERS =================
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def mark_pending(uid: int, category: str, item: str):
    PENDING_PAYMENT[uid] = {"category": category, "item": item}

def user_tag(u) -> str:
    name = " ".join(x for x in [u.first_name or "", u.last_name or ""] if x).strip() or (f"@{u.username}" if u.username else "بدون‌نام")
    handle = f"@{u.username}" if u.username else f"id:{u.id}"
    return f"{name} ({handle})"

# ================= HANDLERS =================
@bot.message_handler(commands=["start"])
def handle_start(message: Message):
    USERS.add(message.from_user.id)
    bot.send_message(message.chat.id, "سلام 👋\nلطفاً از لیست زیر انتخاب کنید:", reply_markup=main_menu_keyboard())

@bot.callback_query_handler(func=lambda c: True)
def handle_callbacks(call: CallbackQuery):
    data = call.data
    bot.answer_callback_query(call.id)

    # --- VPN ---
    if data == "buy_vpn":
        bot.edit_message_text("🛡️ خرید VPN\nیکی از محصولات را انتخاب کنید:",
                              chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              reply_markup=vpn_menu_keyboard()); return

    if data.startswith("vpn_plan:"):
        idx = int(data.split(":")[1])
        plan_title = VPN_PRODUCTS[idx]
        mark_pending(call.from_user.id, "vpn", plan_title)
        bot.edit_message_text(f"✅ «{plan_title}» انتخاب شد.\n\nبرای ادامه پرداخت:",
                              chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              reply_markup=payment_keyboard()); return

    # --- اپ‌ها ---
    if data == "buy_apps":
        bot.edit_message_text("🛍️ خرید اشتراک اپلیکیشن‌ها\nیکی از اپلیکیشن‌ها را انتخاب کنید:",
                              chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              reply_markup=apps_menu_keyboard()); return

    if data.startswith("app:"):
        app_key = data.split(":")[1]
        bot.edit_message_text(f"{APPS[app_key]['title']}\nیکی از پلن‌ها را انتخاب کنید:",
                              chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              reply_markup=app_plans_keyboard(app_key)); return

    if data.startswith("app_plan:"):
        _, app_key, idx = data.split(":")
        plan_title = f"{APPS[app_key]['title']} — {APPS[app_key]['plans'][int(idx)]}"
        mark_pending(call.from_user.id, "app", plan_title)
        bot.edit_message_text(f"✅ {plan_title}\n\nبرای ادامه پرداخت:",
                              chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              reply_markup=payment_keyboard()); return

    # --- پرداخت ---
    if data == "pay_card":
        bot.send_message(call.message.chat.id,
                         f"💳 شماره کارت برای واریز:\n<code>{CARD_NUMBER}</code>\n\n"
                         "✅ لطفاً بعد از پرداخت، اسکرین‌شات تراکنش را همینجا ارسال کنید.")
        return

    # --- تماس با پشتیبانی ---
    if data == "contact_us":
        bot.edit_message_text("📞 تماس با پشتیبانی\nبرای گفتگو مستقیم با پشتیبان، روی دکمه زیر بزنید:",
                              chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              reply_markup=contact_keyboard()); return

    # --- ادمین ---
    if data == "admin_login":
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "دسترسی ندارید.", show_alert=True); return
        bot.edit_message_text("🔐 پنل ادمین — یک گزینه را انتخاب کنید:",
                              chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              reply_markup=admin_menu_keyboard()); return

    if data.startswith("admin:"):
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "دسترسی ندارید.", show_alert=True); return
        action = data.split(":", 1)[1]

        if action == "users_count":
            bot.send_message(call.message.chat.id, f"👥 تعداد کاربران: {len(USERS)}"); return

        if action == "export_users":
            buf = io.StringIO(); w = csv.writer(buf); w.writerow(["user_id"])
            for uid in sorted(USERS): w.writerow([uid])
            datafile = io.BytesIO(buf.getvalue().encode("utf-8")); datafile.name = "users.csv"
            bot.send_document(call.message.chat.id, datafile, caption="📤 خروجی کاربران"); return

        if action == "maintenance":
            MAINTENANCE["enabled"] = not MAINTENANCE["enabled"]
            status = "فعال شد ✅" if MAINTENANCE["enabled"] else "غیرفعال شد ❌"
            bot.send_message(call.message.chat.id, f"🛠 حالت تعمیرات {status}"); return

        if action == "list_vpn":
            text = "🛒 لیست محصولات VPN:\n• " + "\n• ".join(VPN_PRODUCTS)
            bot.send_message(call.message.chat.id, text); return

        if action == "list_apps":
            lines = []
            for _, app in APPS.items():
                lines.append(app['title'])
                for p in app["plans"]:
                    lines.append(f"   └ {p}")
            bot.send_message(call.message.chat.id, "🛍 لیست اشتراک اپ‌ها:\n" + "\n".join(lines)); return

        if action == "broadcast":
            # NEW: enter "awaiting draft" mode for this admin
            BROADCAST_AWAIT.add(call.from_user.id)
            BROADCAST_DRAFT.pop(call.from_user.id, None)
            bot.send_message(
                call.message.chat.id,
                "📣 لطفاً محتوای پیام همگانی را ارسال کنید.\n"
                "می‌تواند متن، عکس، ویدیو یا سند باشد. بعد از ارسال، پیش‌نمایش و دکمهٔ تأیید را می‌بینید."
            )
            return

    # --- Broadcast confirm buttons (send/cancel) ---
    if data == "admin:broadcast_send":
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "دسترسی ندارید.", show_alert=True); return

        draft = BROADCAST_DRAFT.get(call.from_user.id)
        if not draft:
            bot.answer_callback_query(call.id, "پیش‌نویسی وجود ندارد.", show_alert=True); return

        from_chat_id = draft["from_chat_id"]
        message_id = draft["message_id"]

        sent_ok = 0
        sent_fail = 0

        # NOTE: Use copy_message to keep content formatting/media intact
        for uid in list(USERS):
            try:
                bot.copy_message(chat_id=uid, from_chat_id=from_chat_id, message_id=message_id)
                sent_ok += 1
                time.sleep(0.05)  # tiny delay to reduce flood risk
            except Exception:
                sent_fail += 1
                # continue to next user

        BROADCAST_AWAIT.discard(call.from_user.id)
        BROADCAST_DRAFT.pop(call.from_user.id, None)

        bot.edit_message_text(
            f"✅ ارسال همگانی پایان یافت.\n"
            f"موفق: {sent_ok}\n"
            f"ناموفق: {sent_fail}",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
        return

    if data == "admin:broadcast_cancel":
        if not is_admin(call.from_user.id):
            bot.answer_callback_query(call.id, "دسترسی ندارید.", show_alert=True); return

        BROADCAST_AWAIT.discard(call.from_user.id)
        BROADCAST_DRAFT.pop(call.from_user.id, None)
        bot.edit_message_text(
            "❌ ارسال همگانی لغو شد.",
            chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
        return

    # --- بازگشت ---
    if data == "back_main":
        bot.edit_message_text("لطفاً از لیست زیر انتخاب کنید:",
                              chat_id=call.message.chat.id,
                              message_id=call.message.message_id,
                              reply_markup=main_menu_keyboard()); return

# ================= دریافت اسکرین‌شات / سند پرداخت =================
def approve_keyboard(user_id: int):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ تأیید", callback_data=f"approve:{user_id}"),
        types.InlineKeyboardButton("❌ رد", callback_data=f"reject:{user_id}")
    )
    return kb

# ================= دریافت اسکرین‌شات / سند پرداخت =================
@bot.message_handler(content_types=["photo", "document"])
def handle_payment_proof(message: Message):
    uid = message.from_user.id
    order = PENDING_PAYMENT.get(uid)
    if not order:
        return

    caption = (f"🧾 رسید پرداخت جدید\n"
               f"کاربر: {user_tag(message.from_user)}\n"
               f"سفارش: {order['item']}\n"
               f"شناسه چت: <code>{message.chat.id}</code>\n"
               f"زمان: {message.date}")

    # Send to all admins WITH approve/reject buttons
    for admin_id in ADMIN_IDS:
        try:
            if message.content_type == "photo":
                file_id = message.photo[-1].file_id
                bot.send_photo(admin_id, file_id, caption=caption, reply_markup=approve_keyboard(uid))
            else:
                file_id = message.document.file_id
                bot.send_document(admin_id, file_id, caption=caption, reply_markup=approve_keyboard(uid))
        except Exception:
            pass

    # Notify user we received proof
    bot.reply_to(message, "✅ رسید پرداخت دریافت شد.\nپشتیبانی بررسی خواهد کرد.")


# ================= دریافت اسکرین‌شات / سند پرداخت =================
@bot.message_handler(content_types=["photo", "document"])
def handle_payment_proof(message: Message):
    uid = message.from_user.id
    order = PENDING_PAYMENT.get(uid)
    if not order:
        return

    caption = (f"🧾 رسید پرداخت جدید\n"
               f"کاربر: {user_tag(message.from_user)}\n"
               f"سفارش: {order['item']}\n"
               f"شناسه چت: <code>{message.chat.id}</code>\n"
               f"زمان: {message.date}")

    # Send to all admins WITH approve/reject buttons
    for admin_id in ADMIN_IDS:
        try:
            if message.content_type == "photo":
                file_id = message.photo[-1].file_id
                bot.send_photo(admin_id, file_id, caption=caption, reply_markup=approve_keyboard(uid))
            else:
                file_id = message.document.file_id
                bot.send_document(admin_id, file_id, caption=caption, reply_markup=approve_keyboard(uid))
        except Exception:
            pass

    # Notify user we received proof
    bot.reply_to(message, "✅ رسید پرداخت دریافت شد.\nپشتیبانی بررسی خواهد کرد.")



# ================= NEW: Admin broadcast draft catcher =================
@bot.message_handler(content_types=[
    "text", "photo", "video", "animation", "document", "audio", "voice", "video_note"
])
def handle_admin_broadcast_draft(message: Message):
    """If an admin is awaiting a broadcast draft, capture the message as the draft."""
    if message.from_user.id not in BROADCAST_AWAIT:
        return  # not in broadcast mode; let other handlers (if any) process or ignore

    if not is_admin(message.from_user.id):
        return

    # Save draft location (we will copy this message to all users)
    BROADCAST_DRAFT[message.from_user.id] = {
        "from_chat_id": message.chat.id,
        "message_id": message.message_id
    }
    BROADCAST_AWAIT.discard(message.from_user.id)

    # Show a confirmation UI to the admin
    # We can't "preview" with exact content inline, but we confirm it's recorded.
    bot.reply_to(
        message,
        "پیش‌نویس پیام همگانی ذخیره شد. برای ارسال به همه روی «ارسال به همه» بزنید.",
        reply_markup=broadcast_confirm_keyboard()
    )

# ================= RUN =================
if __name__ == "__main__":
    print("Bot is running…")
    bot.infinity_polling(skip_pending=True, timeout=30)
