"""WeatherSkill — 天气查询技能.

数据源: python-weather (免费，无需 API Key)
支持中国及全球城市实时天气 + 3天预报。
"""

from __future__ import annotations

import re
from typing import Any

import python_weather

from app.skills.base import BaseSkill, SkillContext, SkillManifest
from app.utils.logger import logger

# ─── 常见中文城市 → 英文名 ───────────────────────────────────────────────

_CITY_MAP: dict[str, str] = {
    "北京": "Beijing", "上海": "Shanghai", "广州": "Guangzhou",
    "深圳": "Shenzhen", "杭州": "Hangzhou", "南京": "Nanjing",
    "成都": "Chengdu", "重庆": "Chongqing", "武汉": "Wuhan",
    "西安": "Xian", "天津": "Tianjin", "苏州": "Suzhou",
    "长沙": "Changsha", "青岛": "Qingdao", "大连": "Dalian",
    "厦门": "Xiamen", "昆明": "Kunming", "合肥": "Hefei",
    "郑州": "Zhengzhou", "济南": "Jinan", "福州": "Fuzhou",
    "哈尔滨": "Harbin", "沈阳": "Shenyang", "长春": "Changchun",
    "石家庄": "Shijiazhuang", "太原": "Taiyuan", "南昌": "Nanchang",
    "贵阳": "Guiyang", "兰州": "Lanzhou", "海口": "Haikou",
    "三亚": "Sanya", "拉萨": "Lhasa", "银川": "Yinchuan",
    "西宁": "Xining", "呼和浩特": "Hohhot", "乌鲁木齐": "Urumqi",
    "南宁": "Nanning", "无锡": "Wuxi", "宁波": "Ningbo",
    "东莞": "Dongguan", "佛山": "Foshan", "珠海": "Zhuhai",
    "温州": "Wenzhou",
    "东京": "Tokyo", "首尔": "Seoul", "纽约": "New York",
    "伦敦": "London", "巴黎": "Paris", "洛杉矶": "Los Angeles",
    "旧金山": "San Francisco", "悉尼": "Sydney", "新加坡": "Singapore",
    "曼谷": "Bangkok", "香港": "Hong Kong", "台北": "Taipei",
    "澳门": "Macau",
}


def _translate_city(city: str) -> str:
    return _CITY_MAP.get(city.strip(), city.strip())


def _extract_city(text: str) -> str:
    for pat in [
        r"(?:查一?下?|看一?下?|搜一?下?)?(.{1,8}?)(?:的|市)?(?:天气|气温|温度)",
        r"(?:weather|forecast)\s+(?:in\s+)?(\S+)",
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            city = m.group(1).strip()
            city = re.sub(r"^(今天|明天|后天|现在|实时|最新)", "", city).strip()
            if city:
                return city
    return ""


class WeatherSkill(BaseSkill):
    """天气查询技能 — 免费，无需 API Key。"""

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="weather",
            description="查询中国及全球城市实时天气（免费，无需 API Key）",
            usage_examples=[
                "北京天气怎么样",
                "上海今天天气",
                "查一下东京的天气",
                "深圳气温多少",
            ],
            version="0.1.0",
            trigger_patterns=[
                r"天气", r"气温", r"温度",
                r"weather", r"forecast",
                r"几度", r"冷不冷", r"热不热", r"下雨", r"下雪",
            ],
            actions_doc='子动作: query（查询城市天气，params: {city: "城市名"}）',
        )

    async def run(self, params: dict[str, Any], context: SkillContext) -> str:
        city = params.get("city", "") or params.get("query", "")
        if not city:
            city = _extract_city(context.user_message)
        if not city:
            return "🌤 请告诉我你想查哪个城市的天气。\n\n示例：北京天气、上海天气、东京天气"

        en_city = _translate_city(city)
        logger.info("WeatherSkill: query=%s → %s", city, en_city)

        try:
            async with python_weather.Client(unit=python_weather.METRIC) as client:
                weather = await client.get(en_city)

            temp = weather.temperature
            description = weather.description or (weather.kind.name if weather.kind else "")
            humidity = weather.humidity
            wind_speed = weather.wind_speed

            lines = [
                f"🌤 **{city}** 实时天气",
                "",
                f"🌡 温度: {temp}°C",
                f"☁️ 天气: {description}",
                f"💧 湿度: {humidity}%",
                f"💨 风速: {wind_speed} km/h",
            ]

            forecasts = list(weather.daily_forecasts)
            if forecasts:
                lines.append("")
                lines.append("📅 **未来天气预报**")
                for day in forecasts[:3]:
                    date_str = day.date.strftime("%m/%d")
                    lines.append(
                        f"  {date_str}: {day.lowest_temperature}°C ~ {day.highest_temperature}°C"
                    )

            return "\n".join(lines)

        except Exception as e:
            logger.error("WeatherSkill error: %s", e, exc_info=True)
            return f"❌ 查询 {city} 天气失败: {e}\n\n💡 请确认城市名称是否正确，或尝试用英文名。"
