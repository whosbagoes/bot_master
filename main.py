import os 
import json
import logging
import gspread
from datetime import datetime
from google.oauth2.service_account import Credentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
PILIH_BAHAN, INPUT_QTY, INPUT_SATUAN, INPUT_HARGA, KONFIRMASI = range(5)

# ── KONEKSI GOOGLE SHEETS ─────────────────────────────────────────────────────
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

HEADER_PEMBELIAN = ["Tanggal", "User", "Bahan", "Kategori", "Qty", "Satuan", "Harga Aktual"]
HEADER_HARGA     = ["Tanggal", "Bahan", "Harga Lama", "Harga Baru", "Selisih", "User"]
HEADER_SUMMARY   = ["Bulan", "Bahan", "Kategori", "Total Qty", "Total Pengeluaran"]

def init_sheets():
    creds_dict = json.loads(os.environ["GOOGLE_CREDENTIALS_JSON"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    gc = gspread.authorize(creds)
    ss = gc.open_by_key(os.environ["SPREADSHEET_ID"])

    def get_or_create(name, headers):
        try:
            return ss.worksheet(name)
        except gspread.WorksheetNotFound:
            ws = ss.add_worksheet(title=name, rows=1000, cols=len(headers))
            ws.append_row(headers)
            return ws

    return {
        "db":      ss.worksheet("Database Bahan"),
        "beli":    get_or_create("Pembelian", HEADER_PEMBELIAN),
        "harga":   get_or_create("Riwayat Perubahan Harga", HEADER_HARGA),
        "summary": get_or_create("Ringkasan", HEADER_SUMMARY),
    }

ws = init_sheets()

# ── FUNGSI SHEETS ─────────────────────────────────────────────────────────────
def get_all_bahan():
    try:
        result = []
        for r in ws["db"].get_all_records():
            nama = str(r.get("Nama Bahan", "")).strip()
            kat  = str(r.get("Kategori", "")).strip()
            try:
                harga = float(str(r.get("Harga Beli Real", "0")).replace(".", "").replace(",", ""))
            except ValueError:
                harga = 0.0
            if nama:
                result.append({"nama": nama, "kategori": kat, "harga": harga})
        return result
    except Exception as e:
        logger.error(f"get_all_bahan: {e}")
        return []

def catat_pembelian(tgl, user, bahan, kategori, qty, satuan, harga):
    ws["beli"].append_row([tgl, user, bahan, kategori, qty, satuan, harga])
    _update_summary(bahan, kategori, qty, harga)

def catat_perubahan_harga(tgl, bahan, harga_lama, harga_baru, user):
    ws["harga"].append_row([tgl, bahan, harga_lama, harga_baru, harga_baru - harga_lama, user])

def update_harga_referensi(nama_bahan, harga_baru):
    try:
        cell = ws["db"].find(nama_bahan, in_column=2)
        if cell:
            ws["db"].update_cell(cell.row, 4, harga_baru)
    except Exception as e:
        logger.error(f"update_harga_referensi: {e}")

def _update_summary(bahan, kategori, qty, harga):
    bulan = datetime.now().strftime("%Y-%m")
    try:
        for i, r in enumerate(ws["summary"].get_all_records(), start=2):
            if r.get("Bulan") == bulan and r.get("Bahan") == bahan:
                ws["summary"].update(f"D{i}:E{i}", [[
                    float(r.get("Total Qty", 0)) + qty,
                    float(r.get("Total Pengeluaran", 0)) + harga
                ]])
                return
        ws["summary"].append_row([bulan, bahan, kategori, qty, harga])
    except Exception as e:
        logger.error(f"_update_summary: {e}")

def get_riwayat_pembelian(limit=10):
    try:
        records = [r for r in ws["beli"].get_all_records() if r.get("Bahan")]
        return records[-limit:]
    except Exception as e:
        logger.error(f"get_riwayat: {e}")
        return []

def get_perubahan_harga(limit=10):
    try:
        records = [r for r in ws["harga"].get_all_records() if r.get("Bahan")]
        return records[-limit:]
    except Exception as e:
        logger.error(f"get_perubahan_harga: {e}")
        return []

def get_summary():
    bulan = datetime.now().strftime("%Y-%m")
    try:
        return [r for r in ws["summary"].get_all_records()
                if r.get("Bulan") == bulan and r.get("Bahan")]
    except Exception as e:
        logger.error(f"get_summary: {e}")
        return []

# ── MENU UTAMA ────────────────────────────────────────────────────────────────
MENU_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("🛒 Catat Pembelian",       callback_data="beli")],
    [InlineKeyboardButton("📋 Riwayat Pembelian",     callback_data="riwayat")],
    [InlineKeyboardButton("📊 Ringkasan Pengeluaran", callback_data="summary")],
    [InlineKeyboardButton("💰 Perubahan Harga",       callback_data="harga")],
])
BACK_KB = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu Utama", callback_data="menu_utama")]])

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Bot Tracking Bahan Baku*\n\nPilih menu di bawah:",
        reply_markup=MENU_KB, parse_mode="Markdown"
    )

async def menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("👋 *Bot Tracking Bahan Baku*\n\nPilih menu di bawah:",
                               reply_markup=MENU_KB, parse_mode="Markdown")

# ── CATAT PEMBELIAN ───────────────────────────────────────────────────────────
async def mulai_beli(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    bahan_list = get_all_bahan()
    if not bahan_list:
        await q.edit_message_text("❌ Gagal mengambil data bahan. Coba lagi.")
        return ConversationHandler.END

    ctx.user_data["bahan_list"] = bahan_list
    kb, row = [], []
    for i, b in enumerate(bahan_list):
        row.append(InlineKeyboardButton(b["nama"], callback_data=f"bahan_{i}"))
        if len(row) == 2:
            kb.append(row); row = []
    if row: kb.append(row)
    kb.append([InlineKeyboardButton("❌ Batal", callback_data="batal")])

    await q.edit_message_text("🛒 *Catat Pembelian*\n\nPilih bahan yang dibeli:",
                               reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown")
    return PILIH_BAHAN

async def pilih_bahan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    bahan = ctx.user_data["bahan_list"][int(q.data.split("_")[1])]
    ctx.user_data["bahan_dipilih"] = bahan
    await q.edit_message_text(
        f"✅ *{bahan['nama']}*\nKategori: {bahan['kategori']}\n"
        f"Harga referensi: Rp {bahan['harga']:,.0f}\n\nMasukkan *jumlah/qty*:",
        parse_mode="Markdown"
    )
    return INPUT_QTY

async def input_qty(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        qty = float(update.message.text.strip().replace(",", "."))
        assert qty > 0
    except:
        await update.message.reply_text("❌ Masukkan angka yang valid:"); return INPUT_QTY
    ctx.user_data["qty"] = qty
    await update.message.reply_text(f"📦 Qty: *{qty}*\n\nMasukkan *satuan* (kg, gram, pcs, pack...):",
                                     parse_mode="Markdown")
    return INPUT_SATUAN

async def input_satuan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["satuan"] = update.message.text.strip()
    bahan = ctx.user_data["bahan_dipilih"]
    await update.message.reply_text(
        f"💰 Masukkan *harga beli aktual* (total):\n_(Referensi: Rp {bahan['harga']:,.0f})_",
        parse_mode="Markdown"
    )
    return INPUT_HARGA

async def input_harga(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        harga = float(update.message.text.strip().replace(".", "").replace(",", ""))
        assert harga > 0
    except:
        await update.message.reply_text("❌ Masukkan angka harga yang valid:"); return INPUT_HARGA

    ctx.user_data["harga_aktual"] = harga
    bahan   = ctx.user_data["bahan_dipilih"]
    selisih = harga - bahan["harga"]
    ket = ("🔴 Naik" if selisih > 0 else "🟢 Turun" if selisih < 0 else "⚪ Sama")
    ket += f" Rp {abs(selisih):,.0f}" if selisih != 0 else ""

    kb = [[InlineKeyboardButton("✅ Konfirmasi", callback_data="konfirm_ya"),
           InlineKeyboardButton("❌ Batal",      callback_data="batal")]]
    await update.message.reply_text(
        f"📝 *Ringkasan Pembelian:*\n\n"
        f"🏷️ Bahan  : {bahan['nama']}\n"
        f"📦 Qty    : {ctx.user_data['qty']} {ctx.user_data['satuan']}\n"
        f"💰 Harga  : Rp {harga:,.0f}\n"
        f"📊 Selisih: {ket}\n\nKonfirmasi simpan?",
        reply_markup=InlineKeyboardMarkup(kb), parse_mode="Markdown"
    )
    return KONFIRMASI

async def konfirmasi(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    bahan  = ctx.user_data["bahan_dipilih"]
    qty    = ctx.user_data["qty"]
    satuan = ctx.user_data["satuan"]
    harga  = ctx.user_data["harga_aktual"]
    tgl    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    user   = q.from_user.full_name

    catat_pembelian(tgl, user, bahan["nama"], bahan["kategori"], qty, satuan, harga)
    if harga != bahan["harga"]:
        catat_perubahan_harga(tgl, bahan["nama"], bahan["harga"], harga, user)
        update_harga_referensi(bahan["nama"], harga)

    await q.edit_message_text(
        f"✅ *Pembelian berhasil dicatat!*\n"
        f"{'⚠️ Perubahan harga ikut dicatat.' if harga != bahan['harga'] else ''}",
        parse_mode="Markdown"
    )
    await q.message.reply_text("Apa lagi?", reply_markup=MENU_KB)
    return ConversationHandler.END

async def batal(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data.clear()
    await q.edit_message_text("❌ Dibatalkan.")
    await q.message.reply_text("Kembali ke menu:", reply_markup=MENU_KB)
    return ConversationHandler.END

# ── INFO HANDLERS ─────────────────────────────────────────────────────────────
async def lihat_riwayat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = get_riwayat_pembelian()
    if not data:
        await q.edit_message_text("📋 Belum ada data pembelian.", reply_markup=BACK_KB); return
    txt = "📋 *10 Pembelian Terakhir:*\n\n"
    for r in data:
        txt += f"📅 {r['Tanggal']}\n   {r['Bahan']} — {r['Qty']} {r['Satuan']}\n   💰 Rp {float(r['Harga Aktual']):,.0f} — {r['User']}\n\n"
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=BACK_KB)

async def lihat_summary(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = get_summary()
    txt  = "📊 *Ringkasan Pengeluaran (Bulan Ini):*\n\n"
    total = sum(float(r["Total Pengeluaran"]) for r in data)
    for r in data:
        txt += f"• {r['Bahan']}: Rp {float(r['Total Pengeluaran']):,.0f}\n"
    txt += f"\n💵 *Total: Rp {total:,.0f}*"
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=BACK_KB)

async def lihat_perubahan_harga(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    data = get_perubahan_harga()
    if not data:
        await q.edit_message_text("💰 Belum ada perubahan harga.", reply_markup=BACK_KB); return
    txt = "💰 *10 Perubahan Harga Terakhir:*\n\n"
    for r in data:
        selisih = float(r["Harga Baru"]) - float(r["Harga Lama"])
        txt += (f"{'🔴' if selisih > 0 else '🟢'} *{r['Bahan']}*\n"
                f"   {r['Tanggal']}\n"
                f"   Rp {float(r['Harga Lama']):,.0f} → Rp {float(r['Harga Baru']):,.0f}\n\n")
    await q.edit_message_text(txt, parse_mode="Markdown", reply_markup=BACK_KB)

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(os.environ["TELEGRAM_TOKEN"]).build()

    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(mulai_beli, pattern="^beli$")],
        states={
            PILIH_BAHAN:  [CallbackQueryHandler(pilih_bahan, pattern="^bahan_")],
            INPUT_QTY:    [MessageHandler(filters.TEXT & ~filters.COMMAND, input_qty)],
            INPUT_SATUAN: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_satuan)],
            INPUT_HARGA:  [MessageHandler(filters.TEXT & ~filters.COMMAND, input_harga)],
            KONFIRMASI:   [CallbackQueryHandler(konfirmasi, pattern="^konfirm_ya$")],
        },
        fallbacks=[CallbackQueryHandler(batal, pattern="^batal$")],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(lihat_riwayat,         pattern="^riwayat$"))
    app.add_handler(CallbackQueryHandler(lihat_summary,         pattern="^summary$"))
    app.add_handler(CallbackQueryHandler(lihat_perubahan_harga, pattern="^harga$"))
    app.add_handler(CallbackQueryHandler(menu,                  pattern="^menu_utama$"))

    logger.info("Bot berjalan...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
