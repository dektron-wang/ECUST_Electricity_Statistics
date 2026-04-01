"""
ECUST 电量统计系统
支持多宿舍管理、数据库存储、报警推送
"""

import datetime
import logging
import os
import re
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import parse_qs, urlparse

import requests
import tomllib

from database import (
    init_db,
    add_dormitory,
    get_all_dormitories,
    add_electricity_record,
    get_electricity_records,
    export_to_json,
)

# 环境变量配置
DEBUG = os.environ.get("DEBUG", os.environ.get("debug", "")).strip()
GITHUB_TRIGGERING_ACTOR = os.environ.get("GITHUB_TRIGGERING_ACTOR", "").strip()
# 运行模式: report (日报) 或 monitor (监控)
RUN_MODE = os.environ.get("RUN_MODE", "report").strip().lower()

# 加载配置
config = tomllib.loads(Path("config.toml").read_text(encoding="utf-8"))
logging.basicConfig(level=logging.INFO)
logging.info(f"{GITHUB_TRIGGERING_ACTOR=}")

# 默认值
DEFAULT_DAYS_TO_SHOW = 10
DEFAULT_WARNING = 10
DEFAULT_PUSH_WARNING_ONLY = False


def once(func: Callable[..., Any]) -> Callable[..., Any]:
    """Runs a function only once."""
    results: dict[Any, Callable[..., Any]] = {}

    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if func not in results:
            results[func] = func(*args, **kwargs)
        return results[func]

    return wrapper


@once
def get_date() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


def building_number_map(id: int | str) -> int:
    """
    将华理的 buildid 转换为实际楼号（奉贤）
    """
    match int(id):
        case x if x >= 27 and x <= 46:
            return x - 22
        case x if x >= 49 and x <= 52:
            return x - 24
        case other:
            return other


def get_headers() -> dict[str, str]:
    """获取请求 Headers (鉴权使用)"""
    return {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Connection": "keep-alive",
        "Host": "yktyd.ecust.edu.cn",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "User-Agent": "Mozilla/5.0 (Linux; U; Android 4.1.2; zh-cn; Chitanda/Akari) AppleWebKit/534.30 (KHTML, like Gecko) Version/4.0 Mobile Safari/534.30 MicroMessenger/6.0.0.58_r884092.501 NetType/WIFI",
    }


def build_url(buildid: int, roomid: str) -> str:
    """构建请求 URL"""
    return f"https://yktyd.ecust.edu.cn/WxRes.aspx?buildid={buildid}&roomid={roomid}"


def fetch_electricity(buildid: int, roomid: str, url_override: str = None) -> tuple[Optional[float], Optional[float]]:
    """
    获取电量数据和功率

    Returns:
        tuple: (剩余电量 kWh, 功率 kW)
    """
    if url_override:
        url = url_override
    else:
        url = build_url(buildid, roomid)
    headers = get_headers()

    try:
        response = requests.get(url, headers=headers)
        text = response.text

        # 解析剩余电量
        remain_match = re.findall(r"(-?\d+(\.\d+)?)度", text)
        if not remain_match:
            logging.error(f"无法解析电量，buildid={buildid}, roomid={roomid}")
            return None, None

        remain = float(remain_match[0][0])

        # 解析功率 (常见格式：功率：1.23kW 或 功率:1.23kW)
        power = None
        power_match = re.search(r"功率[：:]\s*(-?\d+(\.\d+)?)\s*[kK][wW]", text)
        if power_match:
            power = float(power_match.group(1))

        # 功率异常检测（负值或过大的值视为异常）
        if power is not None and (power < 0 or power > 100):
            logging.warning(f"功率异常: {power} kW，标记为无数据")
            power = None

        logging.info(f"宿舍 {buildid}-{roomid} 剩余电量：{remain} kWh, 功率：{power if power else '无数据'} kW")
        return remain, power

    except Exception as e:
        logging.exception(e)
        logging.error(f"获取电量失败，buildid={buildid}, roomid={roomid}, response: {response.text}")
        return None, None


def generate_tablestr(records: list[dict]) -> str:
    """生成 Markdown 表格 - 中日双语"""
    tablestr = ["| 日付 / 日期 | 残り電力 / 剩余电量 |\n| --- | --- |"]
    for item in reversed(records):
        tablestr.append(f"| {item['time']} | {item['kWh']} kWh |")
    tablestr.append("")
    return "\n".join(tablestr)


def generate_message(
    dorm: dict,
    records: list[dict],
    latest_kwh: float,
    latest_power: float = None,
    is_warning: bool = False
) -> Optional[str]:
    """生成推送消息 - 东雪莲风格中日双语"""
    text: list[str] = []
    warning_threshold = dorm.get("warning_threshold", DEFAULT_WARNING)

    # 标题
    if is_warning:
        text.append(
            f"""# <text style="color:red;">⚠️ 電力不足警告 / 电量不足警告</text>\n"""
        )
        text.append(
            f"> 残り電力量が閾値を下回っています ({latest_kwh}kWh < {warning_threshold}kWh)\n"
            f"> 剩余电量已低于阈值 ({latest_kwh}kWh < {warning_threshold}kWh)\n"
        )
    else:
        text.append(f"# 📊 電力使用状況レポート / 电量使用报告\n")

    # 当前电量和功率
    power_str = f"{latest_power:.2f} kW" if latest_power is not None else "データなし / 无数据"
    text.extend([
        f"## 💡 現在の残り電力量 / 当前剩余电量\n",
        f"**{latest_kwh} kWh**",
        "",
        f"### ⚡ 現在の消費電力 / 当前功率",
        f"**{power_str}**",
        "",
    ])

    # 宿舍信息
    building_num = building_number_map(dorm["buildid"])
    text.extend([
        f"📍 **宿舍情報 / 宿舍信息**",
        f"- {dorm['name']} ({building_num} 号楼 {dorm['roomid']} 室)",
        f"- 更新日時 / 更新时间：{get_date()}",
        "",
    ])

    # 历史数据（仅在 report 模式下显示）
    if records and RUN_MODE == "report":
        records_to_show = records[:DEFAULT_DAYS_TO_SHOW]
        tablestr = generate_tablestr(records_to_show)
        text.extend([
            f"### 📈 過去{len(records_to_show)}日間のデータ / 最近{len(records_to_show)}天数据",
            "",
            f"{tablestr}",
            "",
        ])

    if GITHUB_TRIGGERING_ACTOR:
        text.append(
            f"[📈 グラフで詳細を見る / 图表显示更多数据](https://{GITHUB_TRIGGERING_ACTOR}.github.io/ecust-electricity-statistics)"
        )

    return "\n".join(text)


def pushplus(text: Optional[str], token: str) -> None:
    """PushPlus 推送"""
    if not token and not DEBUG:
        logging.info("PushPlus Token 未配置，跳过推送")
        return

    if not text:
        return

    from utils import sendMsgToWechat

    with suppress():
        if DEBUG:
            print(text)
        else:
            sendMsgToWechat(
                token, f"{get_date()}华理电费统计", text, "markdown"
            )
        logging.info("PushPlus 推送成功")


def telegram(text: Optional[str], bot_token: str, user_ids: list[str]) -> None:
    """Telegram 推送"""
    if not bot_token:
        logging.info("Telegram Bot Token 未配置，跳过推送")
        return
    if not user_ids:
        logging.info("Telegram User IDs 未配置，跳过推送")
        return

    if not text:
        return

    import telegramify_markdown

    for user_id in user_ids:
        if not user_id:
            continue

        with suppress(ValueError):
            if DEBUG:
                print(text)
                continue

            response = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={
                    "chat_id": int(user_id),
                    "text": telegramify_markdown.markdownify(text),
                    "parse_mode": "MarkdownV2",
                },
                headers={"Content-Type": "application/json"},
            )
            if response.status_code == 200:
                logging.info("Telegram 推送成功")
            else:
                logging.error(f"Telegram 推送失败: {response.status_code}")
                logging.error(response.text)


def parse_dormitory_config(dorm_config: dict) -> dict:
    """解析宿舍配置，支持 URL 或 buildid/roomid 两种方式"""
    result = {
        "name": dorm_config["name"],
        "buildid": None,
        "roomid": None,
        "url": None,
        "warning_threshold": dorm_config.get("warning_threshold", DEFAULT_WARNING),
        "push_warning_only": dorm_config.get("push_warning_only", DEFAULT_PUSH_WARNING_ONLY),
    }

    # 方式一：从 URL 解析
    if "url" in dorm_config:
        result["url"] = dorm_config["url"]
        parsed = urlparse(dorm_config["url"])
        params = parse_qs(parsed.query)
        result["buildid"] = int(params.get("buildid", [None])[0])
        result["roomid"] = params.get("roomid", [None])[0]
    # 方式二：直接配置
    elif "buildid" in dorm_config and "roomid" in dorm_config:
        result["buildid"] = dorm_config["buildid"]
        result["roomid"] = str(dorm_config["roomid"])
    else:
        raise ValueError(f"宿舍 {dorm_config.get('name')} 配置错误：需要 url 或 buildid/roomid")

    if not result["buildid"] or not result["roomid"]:
        raise ValueError(f"宿舍 {dorm_config.get('name')} 配置错误：无法获取 buildid 或 roomid")

    return result


def process_dormitory(dorm_config: dict) -> None:
    """处理单个宿舍的电量采集"""
    # 解析配置
    try:
        parsed_config = parse_dormitory_config(dorm_config)
    except ValueError as e:
        logging.error(str(e))
        return

    buildid = parsed_config["buildid"]
    roomid = parsed_config["roomid"]
    url = parsed_config.get("url")

    # 获取电量和功率
    kwh, power = fetch_electricity(buildid, roomid, url)
    if kwh is None:
        logging.error(f"宿舍 {parsed_config['name']} 电量获取失败")
        return

    # 获取宿舍信息
    dormitory_id = add_dormitory(
        name=parsed_config["name"],
        buildid=buildid,
        roomid=roomid,
        warning_threshold=parsed_config["warning_threshold"],
        push_warning_only=parsed_config["push_warning_only"],
    )

    warning_threshold = parsed_config["warning_threshold"]
    is_low_power = kwh < warning_threshold

    # 获取历史记录
    records = get_electricity_records(dormitory_id, DEFAULT_DAYS_TO_SHOW)
    records_compat = [{"time": r["recorded_date"], "kWh": r["kwh"]} for r in records]

    # 获取宿舍信息
    dorm_info = get_all_dormitories()
    current_dorm = next((d for d in dorm_info if d["id"] == dormitory_id), None)

    if not current_dorm:
        return

    # 根据运行模式决定行为
    if RUN_MODE == "report":
        # 日报模式：保存数据并推送
        add_electricity_record(dormitory_id, get_date(), kwh, power)

        # 生成消息
        message = generate_message(current_dorm, records_compat, kwh, power, is_warning=is_low_power)

        # 推送
        push_plus_token = os.environ.get(f"PUSH_PLUS_TOKEN_{buildid}_{roomid}", "").strip()
        telegram_bot_token = os.environ.get(f"TELEGRAM_BOT_TOKEN_{buildid}_{roomid}", "").strip()
        telegram_users_str = os.environ.get(f"TELEGRAM_USER_IDS_{buildid}_{roomid}", "").strip()
        telegram_user_ids = telegram_users_str.split() if telegram_users_str else []

        logging.info(f"宿舍 {parsed_config['name']} PushPlus Token: {'已配置' if push_plus_token else '未配置'}")
        logging.info(f"宿舍 {parsed_config['name']} Telegram: {'已配置' if telegram_bot_token and telegram_user_ids else '未配置'}")

        if push_plus_token:
            pushplus(message, push_plus_token)
        if telegram_bot_token and telegram_user_ids:
            telegram(message, telegram_bot_token, telegram_user_ids)

    elif RUN_MODE == "monitor":
        # 监控模式：仅在电量不足时报警
        if is_low_power:
            message = generate_message(current_dorm, records_compat, kwh, power, is_warning=True)

            push_plus_token = os.environ.get(f"PUSH_PLUS_TOKEN_{buildid}_{roomid}", "").strip()
            telegram_bot_token = os.environ.get(f"TELEGRAM_BOT_TOKEN_{buildid}_{roomid}", "").strip()
            telegram_users_str = os.environ.get(f"TELEGRAM_USER_IDS_{buildid}_{roomid}", "").strip()
            telegram_user_ids = telegram_users_str.split() if telegram_users_str else []

            if push_plus_token:
                pushplus(message, push_plus_token)
            if telegram_bot_token and telegram_user_ids:
                telegram(message, telegram_bot_token, telegram_user_ids)
        else:
            logging.info(f"宿舍 {parsed_config['name']} 电量充足 ({kwh} kWh)，跳过报警")


def main():
    """主函数"""
    logging.info(f"运行模式: {RUN_MODE}")

    # 初始化数据库
    init_db()

    # 从配置获取宿舍列表
    dormitories = config.get("dormitories", [])

    if not dormitories:
        logging.error("配置文件中没有宿舍信息，请检查 config.toml")
        return

    # 处理每个宿舍
    for dorm_config in dormitories:
        logging.info(f"处理宿舍: {dorm_config.get('name', '未命名')}")
        process_dormitory(dorm_config)

    # 仅在 report 模式下导出数据
    if RUN_MODE == "report":
        output_path = Path("docs/data.json")
        export_to_json(output_path, DEFAULT_DAYS_TO_SHOW)
        logging.info(f"数据已导出到 {output_path}")


if __name__ == "__main__":
    main()
