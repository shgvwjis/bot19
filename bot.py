"""
WalletMonitor - TRX (TRC20) 钱包链上交易监控 Telegram Bot
主入口文件，包含所有 Bot 命令处理和交易监控调度逻辑
"""

import asyncio
import logging
import os
import sys
import time

from dotenv import load_dotenv
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    KeyboardButtonRequestChat,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.error import NetworkError, TimedOut
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from database import Database
from monitor import TronMonitor

# 加载 .env 配置
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY", "")
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "10"))
NOTIFY_CHAT_ID = os.getenv("NOTIFY_CHAT_ID", "")
DB_PATH = os.getenv("DB_PATH", "wallets.db")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# 日志配置
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
)
logger = logging.getLogger(__name__)

# 会话状态
STATE_WAIT_ADDRESS = 1
STATE_WAIT_LABEL = 2

# 全局实例
db = Database(DB_PATH)
PROXY_URL = os.getenv("PROXY_URL", "")
tron_monitor = TronMonitor(api_key=TRONGRID_API_KEY, poll_interval=POLL_INTERVAL, proxy=PROXY_URL)
ENERGY_TRX_ADDRESS = os.getenv("ENERGY_TRX_ADDRESS", "")
TG_PREMIUM_URL = os.getenv("TG_PREMIUM_URL", "")

# 主键盘菜单
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📡 添加监听", "📋 监听列表", "🗑 删除监听"],
        ["⚡️ 能量租赁", "🌟 电报会员", "💰 实时U价"],
        ["👤 查用户ID", "📢 查频道ID", "👥 查群组ID"],
    ],
    resize_keyboard=True,
)


# ======================== 命令处理 ========================


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /start 命令"""
    await update.message.reply_text(
        "🔗 <b>WalletMonitor - 链上钱包监控机器人</b>\n\n"
        "支持监控 TRX (TRC20) 链上交易，实时推送交易通知。\n\n"
        "请使用下方菜单操作：",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KEYBOARD,
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /help 命令"""
    await update.message.reply_text(
        "📖 <b>使用帮助</b>\n\n"
        "📡 <b>添加监听</b> - 添加 TRX(TRC20) 钱包地址监控\n"
        "📋 <b>监听列表</b> - 查看当前所有监听地址\n"
        "🗑 <b>删除监听</b> - 删除已添加的监听地址\n"
        "👤 <b>查用户ID</b> - 查询你的 Telegram 用户 ID\n"
        "📢 <b>查频道ID</b> - 查询频道 ID（需转发频道消息）\n"
        "👥 <b>查群组ID</b> - 查询当前群组 ID\n\n"
        "监控到链上交易后会实时推送通知消息。",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KEYBOARD,
    )


# ======================== 添加监听 ========================


async def add_monitor_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """开始添加监听流程"""
    await update.message.reply_text(
        "📡 <b>添加监听地址</b>\n\n"
        "请输入要监听的钱包地址：\n"
        "⚠️ 目前仅支持 <b>TRX (TRC20)</b> 地址\n\n"
        "输入 /cancel 取消操作",
        parse_mode=ParseMode.HTML,
    )
    return STATE_WAIT_ADDRESS


async def add_monitor_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """接收监听地址"""
    address = update.message.text.strip()

    if not TronMonitor.is_valid_tron_address(address):
        await update.message.reply_text(
            "❌ 地址格式无效！请输入正确的 TRX (TRC20) 地址。\n"
            "TRX 地址以 <b>T</b> 开头，长度为 34 个字符。\n\n"
            "请重新输入地址或输入 /cancel 取消：",
            parse_mode=ParseMode.HTML,
        )
        return STATE_WAIT_ADDRESS

    # 检查是否已存在
    chat_id = update.effective_chat.id
    wallets = db.get_wallets(chat_id)
    for w in wallets:
        if w["address"].upper() == address.upper():
            await update.message.reply_text(
                "⚠️ 该地址已在监听列表中！",
                reply_markup=MAIN_KEYBOARD,
            )
            return ConversationHandler.END

    context.user_data["pending_address"] = address
    await update.message.reply_text(
        f"✅ 地址已确认：\n<code>{address}</code>\n\n"
        "请输入<b>备注名称</b>（方便识别此地址）：\n\n"
        "输入 /cancel 取消操作",
        parse_mode=ParseMode.HTML,
    )
    return STATE_WAIT_LABEL


async def add_monitor_label(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """接收备注名称并完成添加"""
    label = update.message.text.strip()

    if len(label) > 50:
        await update.message.reply_text("❌ 备注名称过长，请控制在50个字符以内：")
        return STATE_WAIT_LABEL

    address = context.user_data.get("pending_address", "")
    if not address:
        await update.message.reply_text(
            "❌ 操作已过期，请重新添加。",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    success = db.add_wallet(address, label, chat_id)

    if success:
        # 设置初始时间戳为当前时间（只监控新交易）
        current_ts = int(time.time() * 1000)
        db.update_last_tx_timestamp(address, current_ts)

        await update.message.reply_text(
            f"✅ <b>监听添加成功！</b>\n\n"
            f"📝 备注: <b>{label}</b>\n"
            f"📍 地址: <code>{address}</code>\n\n"
            f"机器人将实时监控该地址的链上交易。",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
        )
    else:
        await update.message.reply_text(
            "❌ 添加失败，该地址可能已存在。",
            reply_markup=MAIN_KEYBOARD,
        )

    context.user_data.pop("pending_address", None)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消当前操作"""
    context.user_data.pop("pending_address", None)
    await update.message.reply_text(
        "❌ 操作已取消。",
        reply_markup=MAIN_KEYBOARD,
    )
    return ConversationHandler.END


# ======================== 监听列表 ========================


async def list_monitors(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示监听列表"""
    chat_id = update.effective_chat.id
    wallets = db.get_wallets(chat_id)

    if not wallets:
        await update.message.reply_text(
            "📋 <b>监听列表为空</b>\n\n"
            "您还没有添加任何监听地址，请点击「📡 添加监听」开始使用。",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
        )
        return

    text = "📋 <b>您的监听列表：</b>\n\n"
    for i, w in enumerate(wallets, 1):
        addr = w["address"]
        text += f"<b>{i}.</b> {w['label']}\n<code>{addr}</code>\n\n"

    await update.message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KEYBOARD,
    )


# ======================== 删除监听 ========================


async def delete_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示删除选择列表"""
    chat_id = update.effective_chat.id
    wallets = db.get_wallets(chat_id)

    if not wallets:
        await update.message.reply_text(
            "📋 <b>监听列表为空</b>，没有可删除的地址。",
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
        )
        return

    keyboard = []
    for w in wallets:
        addr = w["address"]
        short_addr = f"{addr[:8]}...{addr[-10:]}"
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🗑 {w['label']} | {short_addr}",
                    callback_data=f"del:{addr}",
                )
            ]
        )

    keyboard.append(
        [InlineKeyboardButton("❌ 关闭", callback_data="del:cancel")]
    )

    await update.message.reply_text(
        "🗑 <b>请选择要删除的监听地址：</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理删除回调"""
    query = update.callback_query
    await query.answer()

    data = query.data
    if not data.startswith("del:"):
        return

    address = data[4:]

    if address == "cancel":
        await query.message.edit_text("❌ 已关闭删除面板。")
        return

    chat_id = query.message.chat_id
    wallet_info = db.get_wallet_by_address(address)
    success = db.remove_wallet(address, chat_id)

    if success:
        label = wallet_info["label"] if wallet_info else "未知"
        await query.message.edit_text(
            f"✅ <b>已删除监听</b>\n\n"
            f"📝 备注: {label}\n"
            f"📍 地址: <code>{address}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await query.message.edit_text("❌ 删除失败，可能地址已被移除。")


# ======================== 查询 ID ========================


async def get_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询用户 ID"""
    user = update.effective_user
    await update.message.reply_text(
        f"👤 <b>用户信息</b>\n\n"
        f"用户ID: <code>{user.id}</code>\n"
        f"用户名: @{user.username or '无'}\n"
        f"名称: {user.full_name}",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KEYBOARD,
    )


async def get_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询频道 ID - 弹出频道选择列表"""
    keyboard = ReplyKeyboardMarkup(
        [
            [KeyboardButton("📢 选择频道", request_chat=KeyboardButtonRequestChat(
                request_id=1, chat_is_channel=True
            ))],
            [KeyboardButton("❌ 取消")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "📢 <b>查询频道 ID</b>\n\n"
        "请点击下方按钮，从列表中选择要查询的频道：",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def get_group_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查询群组 ID - 弹出群组选择列表"""
    keyboard = ReplyKeyboardMarkup(
        [
            [KeyboardButton("👥 选择群组", request_chat=KeyboardButtonRequestChat(
                request_id=2, chat_is_channel=False
            ))],
            [KeyboardButton("❌ 取消")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        "👥 <b>查询群组 ID</b>\n\n"
        "请点击下方按钮，从列表中选择要查询的群组：",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ======================== 会话选择回调处理 ========================


async def handle_chat_shared(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户通过选择器选择的频道或群组"""
    chat_shared = update.message.chat_shared
    request_id = chat_shared.request_id
    chat_id = chat_shared.chat_id

    if request_id == 1:
        icon = "📢"
        label = "频道"
    else:
        icon = "👥"
        label = "群组"

    title = ""
    if chat_shared.title:
        title = f"\n名称: {chat_shared.title}"

    await update.message.reply_text(
        f"{icon} <b>{label} ID</b>\n\n"
        f"{label}ID: <code>{chat_id}</code>{title}",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KEYBOARD,
    )


async def handle_cancel_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理取消按钮，恢复主键盘"""
    await update.message.reply_text(
        "已取消。",
        reply_markup=MAIN_KEYBOARD,
    )


# ======================== 能量租赁 ========================


async def energy_rental(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """⚡️ 能量租赁"""
    if not ENERGY_TRX_ADDRESS:
        await update.message.reply_text(
            "❌ 未配置能量租赁地址，请在 .env 中设置 ENERGY_TRX_ADDRESS。",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    await update.message.reply_text(
        "【⚡️能量闪租】\n\n"
        "🟩 转入 <b>3  TRX</b> = 免费 <b>1</b> 笔转账 对面有<b>U</b>\n\n"
        "🟨 转入 <b>6  TRX</b> = 免费 <b>2</b> 笔转账 对面没<b>U</b>\n\n"
        "✅对方没U或交易所需要两笔能量\n"
        "🏵使用能量可以节省百分之90转U手续费\n"
        "💎请在1小时内转账，否则过期回收\n"
        "✅转账之前在这个地址转TRX一下即可\n"
        "❗️点击地址即可复制\n\n"
        f"<code>{ENERGY_TRX_ADDRESS}</code>\n\n"
        "➖➖➖➖➖➖➖➖➖\n"
        "祝老板们天天爆单，好运连连。",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KEYBOARD,
    )


# ======================== 电报会员 ========================


async def tg_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """🌟 电报会员"""
    if not TG_PREMIUM_URL:
        await update.message.reply_text(
            "❌ 未配置电报会员链接，请在 .env 中设置 TG_PREMIUM_URL。",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🌟 前往开通电报会员", url=TG_PREMIUM_URL)]]
    )
    await update.message.reply_text(
        "🌟 <b>电报会员</b>\n\n"
        "点击下方按钮前往开通 Telegram Premium：",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


# ======================== 实时U价 ========================


async def usdt_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """💰 实时U价 - 查询 C2C 商户汇率"""
    await update.message.reply_text("💰 正在查询实时 USDT 汇率，请稍候...")

    try:
        import httpx

        headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
        binance_url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"

        results = {}
        async with httpx.AsyncClient(timeout=15, headers=headers) as client:
            for trade_type, label in [("SELL", "buy"), ("BUY", "sell")]:
                r = await client.post(binance_url, json={
                    "fiat": "CNY", "page": 1, "rows": 10, "tradeType": trade_type,
                    "asset": "USDT", "countries": [], "proMerchantAds": False,
                    "publisherType": None, "payTypes": [],
                })
                data = r.json()
                results[label] = data.get("data", [])

        sell_orders = results.get("buy", [])  # 商户卖U给你
        buy_orders = results.get("sell", [])  # 商户买你的U

        if not sell_orders and not buy_orders:
            await update.message.reply_text(
                "❌ 暂无商户挂单数据。",
                reply_markup=MAIN_KEYBOARD,
            )
            return

        text = "[ 实时报价 - USDT/CNY ]\n\n"

        if sell_orders:
            text += "<b>📗 买入U（商户出售）</b>\n"
            for order in sell_orders[:5]:
                adv = order.get("adv", {})
                advertiser = order.get("advertiser", {})
                price = adv.get("price", "--")
                nick = advertiser.get("nickName", "未知")
                text += f"<b>{price}</b>    <code>{nick}</code>\n"

            # 三档价格
            if len(sell_orders) >= 3:
                p1 = sell_orders[0].get("adv", {}).get("price", "0")
                p2 = sell_orders[1].get("adv", {}).get("price", "0")
                p3 = sell_orders[2].get("adv", {}).get("price", "0")
                try:
                    avg = round((float(p1) + float(p2) + float(p3)) / 3, 2)
                except (ValueError, ZeroDivisionError):
                    avg = p1
                text += f"\n实时价格 (三档)：\n1.0 * {avg} = <b>{avg} CNY</b>\n"

        if buy_orders:
            text += f"\n<b>📕 卖出U（商户收购）</b>\n"
            for order in buy_orders[:5]:
                adv = order.get("adv", {})
                advertiser = order.get("advertiser", {})
                price = adv.get("price", "--")
                nick = advertiser.get("nickName", "未知")
                text += f"<b>{price}</b>    <code>{nick}</code>\n"

        text += "\n⏱ 数据来源: Binance C2C"

        await update.message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=MAIN_KEYBOARD,
        )
    except asyncio.TimeoutError:
        await update.message.reply_text(
            "❌ 查询超时，请稍后再试。",
            reply_markup=MAIN_KEYBOARD,
        )
    except Exception as e:
        logger.error(f"查询 USDT 汇率异常: {e}")
        await update.message.reply_text(
            "❌ 查询失败，请稍后再试。",
            reply_markup=MAIN_KEYBOARD,
        )


# ======================== U 换算计算器 ========================

# 缓存汇率，避免每次都请求
_rate_cache: dict = {"rate": None, "ts": 0}


async def _get_usdt_rate() -> float | None:
    """获取当前 USDT/CNY 汇率（取前三商户均价），带60秒缓存"""
    import time as _time
    now = _time.time()
    if _rate_cache["rate"] and now - _rate_cache["ts"] < 60:
        return _rate_cache["rate"]
    try:
        import httpx
        headers = {"User-Agent": "Mozilla/5.0"}
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            r = await client.post(
                "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search",
                json={"fiat": "CNY", "page": 1, "rows": 5, "tradeType": "SELL",
                      "asset": "USDT", "countries": [], "proMerchantAds": False,
                      "publisherType": None, "payTypes": []},
            )
        items = r.json().get("data", [])
        prices = [float(i["adv"]["price"]) for i in items[:3] if i.get("adv", {}).get("price")]
        if not prices:
            return None
        rate = round(sum(prices) / len(prices), 4)
        _rate_cache["rate"] = rate
        _rate_cache["ts"] = now
        return rate
    except Exception:
        return None


async def handle_usdt_calc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 100u / 1U 等输入，换算成人民币"""
    import re
    text = update.message.text.strip()
    m = re.match(r"^([\d]+\.?[\d]*)\s*[uU]$", text)
    if not m:
        return
    amount = float(m.group(1))
    rate = await _get_usdt_rate()
    if rate is None:
        await update.message.reply_text("❌ 获取汇率失败，请稍后再试。")
        return
    cny = round(amount * rate, 2)
    await update.message.reply_text(
        f"💱 <b>{amount:g} USDT</b> ≈ <b>{cny:,.2f} CNY</b>\n"
        f"<i>当前汇率: 1 USDT = {rate} CNY（Binance C2C 均价）</i>",
        parse_mode=ParseMode.HTML,
    )


# ======================== 交易监控任务 ========================


async def monitor_task(application: Application):
    """后台交易监控任务"""
    logger.info("交易监控任务已启动")

    while True:
        try:
            wallets = db.get_all_wallets()

            for wallet in wallets:
                address = wallet["address"]
                label = wallet["label"]
                chat_id = wallet["chat_id"]
                last_ts = wallet["last_tx_timestamp"]

                # 跳过时间戳为 0 的（理论上不会出现，启动时已重置）
                if last_ts == 0:
                    current_ts = int(time.time() * 1000)
                    db.update_last_tx_timestamp(address, current_ts)
                    continue

                # 查询 TRC20 交易
                min_ts = last_ts + 1
                trc20_txs = await tron_monitor.get_trc20_transactions(
                    address, min_timestamp=min_ts
                )

                # 查询 TRX 原生交易
                trx_txs = await tron_monitor.get_trx_transactions(
                    address, min_timestamp=min_ts
                )

                new_max_ts = last_ts

                # 处理 TRC20 交易
                for tx in reversed(trc20_txs):
                    tx_info = tron_monitor.parse_trc20_transaction(tx, address)
                    if tx_info is None:
                        continue

                    block_ts = tx_info["block_timestamp"]
                    if block_ts <= last_ts:
                        continue

                    if block_ts > new_max_ts:
                        new_max_ts = block_ts

                    # 发送通知
                    await send_tx_notification(
                        application, chat_id, tx_info, label
                    )

                # 处理 TRX 原生交易
                for tx in reversed(trx_txs):
                    tx_info = tron_monitor.parse_trx_transaction(tx, address)
                    if tx_info is None:
                        continue

                    block_ts = tx_info["block_timestamp"]
                    if block_ts <= last_ts:
                        continue

                    if block_ts > new_max_ts:
                        new_max_ts = block_ts

                    await send_tx_notification(
                        application, chat_id, tx_info, label
                    )

                # 更新时间戳
                if new_max_ts > last_ts:
                    db.update_last_tx_timestamp(address, new_max_ts)

                # 避免 API 请求过于频繁
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"监控任务异常: {e}", exc_info=True)

        await asyncio.sleep(POLL_INTERVAL)


async def send_tx_notification(
    application: Application,
    chat_id: int,
    tx_info: dict,
    label: str,
):
    """发送交易通知消息"""
    message = tron_monitor.format_notification(tx_info, label)

    # 查看交易详情按钮
    tx_url = f"https://tronscan.org/#/transaction/{tx_info['tx_id']}"
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔗 查看交易详情", url=tx_url)]]
    )

    try:
        await application.bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        logger.info(f"已发送交易通知: {tx_info['tx_id'][:16]}... -> chat:{chat_id}")
    except Exception as e:
        logger.error(f"发送通知失败: {e}")


# ======================== 启动入口 ========================


async def post_init(application: Application):
    """Bot 启动后执行"""
    # 重置所有钱包时间戳为当前时间，只监控启动后的新交易
    current_ts = int(time.time() * 1000)
    db.reset_all_timestamps(current_ts)
    logger.info("已重置所有钱包时间戳，仅监控启动后的新交易")
    asyncio.create_task(monitor_task(application))
    logger.info("WalletMonitor Bot 已启动")


def main():
    if not BOT_TOKEN:
        logger.error("未配置 BOT_TOKEN，请在 .env 文件中设置")
        sys.exit(1)

    if not TRONGRID_API_KEY:
        logger.warning("未配置 TRONGRID_API_KEY，API 请求可能受限")

    if PROXY_URL:
        request = HTTPXRequest(
            proxy=PROXY_URL,
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
        )
        application = Application.builder().token(BOT_TOKEN).request(request).post_init(post_init).build()
    else:
        application = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # 包装菜单处理器：在会话中点击其他按钮时先结束会话再执行对应功能
    def _make_fallback(handler_func):
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            context.user_data.pop("pending_address", None)
            await handler_func(update, context)
            return ConversationHandler.END
        return wrapper

    menu_fallback_handlers = [
        MessageHandler(filters.Regex(r"^📡 添加监听$"), add_monitor_start),
        MessageHandler(filters.Regex(r"^📋 监听列表$"), _make_fallback(list_monitors)),
        MessageHandler(filters.Regex(r"^🗑 删除监听$"), _make_fallback(delete_monitor)),
        MessageHandler(filters.Regex(r"^👤 查用户ID$"), _make_fallback(get_user_id)),
        MessageHandler(filters.Regex(r"^📢 查频道ID$"), _make_fallback(get_channel_id)),
        MessageHandler(filters.Regex(r"^👥 查群组ID$"), _make_fallback(get_group_id)),
        MessageHandler(filters.Regex(r"^⚡️ 能量租赁$"), _make_fallback(energy_rental)),
        MessageHandler(filters.Regex(r"^🌟 电报会员$"), _make_fallback(tg_premium)),
        MessageHandler(filters.Regex(r"^💰 实时U价$"), _make_fallback(usdt_price)),
    ]

    # 添加监听会话处理
    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(r"^📡 添加监听$"), add_monitor_start),
        ],
        states={
            STATE_WAIT_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_monitor_address),
            ],
            STATE_WAIT_LABEL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_monitor_label),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            *menu_fallback_handlers,
        ],
    )

    # 注册处理器
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler)
    application.add_handler(
        MessageHandler(filters.Regex(r"^📋 监听列表$"), list_monitors)
    )
    application.add_handler(
        MessageHandler(filters.Regex(r"^🗑 删除监听$"), delete_monitor)
    )
    application.add_handler(
        MessageHandler(filters.Regex(r"^👤 查用户ID$"), get_user_id)
    )
    application.add_handler(
        MessageHandler(filters.Regex(r"^📢 查频道ID$"), get_channel_id)
    )
    application.add_handler(
        MessageHandler(filters.Regex(r"^👥 查群组ID$"), get_group_id)
    )
    application.add_handler(
        MessageHandler(filters.Regex(r"^⚡️ 能量租赁$"), energy_rental)
    )
    application.add_handler(
        MessageHandler(filters.Regex(r"^🌟 电报会员$"), tg_premium)
    )
    application.add_handler(
        MessageHandler(filters.Regex(r"^💰 实时U价$"), usdt_price)
    )
    application.add_handler(CallbackQueryHandler(delete_callback, pattern=r"^del:"))
    application.add_handler(
        MessageHandler(filters.StatusUpdate.CHAT_SHARED, handle_chat_shared)
    )
    application.add_handler(
        MessageHandler(filters.Regex(r"^❌ 取消$"), handle_cancel_keyboard)
    )
    # 隐藏功能：输入 100u / 1U 等自动换算人民币（放最后，避免拦截其他消息）
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_usdt_calc)
    )

    # 网络错误降级为 DEBUG，避免代理断线时刷屏 ERROR 日志
    async def _handle_network_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        if isinstance(context.error, (NetworkError, TimedOut)):
            logger.debug("网络波动（已自动重连）: %s", context.error)
        else:
            raise context.error

    application.add_error_handler(_handle_network_error)

    logger.info("正在启动 WalletMonitor Bot...")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
