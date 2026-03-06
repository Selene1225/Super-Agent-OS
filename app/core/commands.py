"""Slash command framework — `/` prefix commands bypass LLM routing.

Phase 5.1: Commands are registered in a dict and dispatched by the Agent
before any LLM intent classification. Each handler receives the raw
argument string and the Agent instance, and returns a plain-text reply.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from app.utils.logger import logger

if TYPE_CHECKING:
    from app.core.agent import Agent

# Type alias for command handlers
CommandHandler = Callable[[str, "Agent", str], Coroutine[Any, Any, str]]

# ─── Command registry ────────────────────────────────────────────────────

_commands: dict[str, CommandHandler] = {}
_command_descriptions: dict[str, str] = {}

_start_time: float = time.time()


def slash_command(name: str, description: str):
    """Decorator to register a slash command handler."""

    def decorator(fn: CommandHandler) -> CommandHandler:
        _commands[name] = fn
        _command_descriptions[name] = description
        return fn

    return decorator


async def dispatch(raw_message: str, agent: "Agent", chat_id: str) -> str | None:
    """Parse and dispatch a slash command. Returns None if not a command."""
    text = raw_message.strip()
    if not text.startswith("/"):
        return None

    parts = text.split(maxsplit=1)
    cmd_name = parts[0].lower()  # e.g. "/status"
    args = parts[1].strip() if len(parts) > 1 else ""

    handler = _commands.get(cmd_name)
    if handler is None:
        available = ", ".join(sorted(_commands.keys()))
        return f"未知命令: {cmd_name}\n可用命令: {available}\n输入 /help 查看帮助"

    logger.info("Slash command: %s (args=%r, chat_id=%s)", cmd_name, args, chat_id)
    try:
        return await handler(args, agent, chat_id)
    except Exception as e:
        logger.error("Command %s failed: %s", cmd_name, e, exc_info=True)
        return f"命令 {cmd_name} 执行出错: {e}"


# ═══════════════════════════════════════════════════════════════════════════
# Command implementations
# ═══════════════════════════════════════════════════════════════════════════


@slash_command("/help", "显示所有可用命令")
async def cmd_help(args: str, agent: "Agent", chat_id: str) -> str:
    lines = ["📖 **SAO 可用命令**", ""]
    for cmd, desc in sorted(_command_descriptions.items()):
        lines.append(f"  {cmd}  —  {desc}")
    lines.append("")
    lines.append("直接输入自然语言即可对话或调用技能。")
    return "\n".join(lines)


@slash_command("/status", "显示系统运行状态")
async def cmd_status(args: str, agent: "Agent", chat_id: str) -> str:
    from app.skills.reminder.scheduler import get_scheduler

    # Uptime
    uptime_sec = int(time.time() - _start_time)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"

    # Model info
    factory = agent.factory
    primary = factory.primary_model_name or "未配置"
    all_models = factory.available_models() or ["无"]

    # Skills
    skill_count = len(agent.skills)
    skill_names = ", ".join(sorted(agent.skills.keys())) if agent.skills else "无"

    # Scheduler
    try:
        scheduler = get_scheduler()
        pending_jobs = len(scheduler.get_jobs())
        scheduler_status = f"运行中 ({pending_jobs} 个待执行任务)"
    except AssertionError:
        scheduler_status = "未启动"

    # Memory stats
    try:
        from app.core.memory import get_memory_stats
        mem_stats = get_memory_stats()
        memory_line = f"🧠 记忆: {mem_stats['total_memories']} 条长期记忆, {mem_stats['total_messages']} 条消息, {mem_stats['total_sessions']} 个会话"
    except Exception:
        memory_line = "🧠 记忆: 未初始化"

    lines = [
        "📊 **SAO 系统状态**",
        "",
        f"⏱ 运行时间: {uptime_str}",
        f"🤖 当前模型: {primary}",
        f"   可用模型: {', '.join(all_models)}",
        f"🔧 已加载技能: {skill_count} ({skill_names})",
        f"⏰ 调度器: {scheduler_status}",
        memory_line,
    ]
    return "\n".join(lines)


@slash_command("/skills", "列出已安装技能及版本")
async def cmd_skills(args: str, agent: "Agent", chat_id: str) -> str:
    if not agent.skills:
        return "当前没有已安装的技能。"

    lines = ["🔧 **已安装技能**", ""]
    for name, skill in sorted(agent.skills.items()):
        m = skill.manifest
        examples = "、".join(f"「{e}」" for e in m.usage_examples[:2])
        lines.append(f"  • **{m.name}** v{m.version}")
        lines.append(f"    {m.description}")
        if examples:
            lines.append(f"    示例: {examples}")
        lines.append("")
    return "\n".join(lines)


@slash_command("/new", "重置当前对话历史")
async def cmd_new(args: str, agent: "Agent", chat_id: str) -> str:
    # Clear in-memory history
    agent.histories[chat_id] = []

    # Clear SQLite history
    deleted = 0
    try:
        from app.core.memory import clear_history
        deleted = clear_history(chat_id)
    except Exception as e:
        logger.warning("Failed to clear SQLite history: %s", e)

    if deleted > 0:
        return f"🔄 对话已重置（清除 {deleted} 条消息），开始新的会话。\n长期记忆不受影响。"
    else:
        return "当前没有对话历史，无需重置。"


@slash_command("/model", "切换主模型 (用法: /model qwen)")
async def cmd_model(args: str, agent: "Agent", chat_id: str) -> str:
    factory = agent.factory
    available = factory.available_models()

    if not args:
        current = factory.primary_model_name or "未配置"
        models_list = ", ".join(available) if available else "无"
        return f"当前模型: {current}\n可用模型: {models_list}\n用法: /model <模型名>"

    target = args.lower().strip()

    success = factory.switch_primary(target)
    if success:
        return f"✅ 已切换到模型: {factory.primary_model_name}"
    else:
        models_list = ", ".join(available) if available else "无"
        return f"❌ 模型 '{target}' 不可用\n可用模型: {models_list}"


@slash_command("/doctor", "系统自检")
async def cmd_doctor(args: str, agent: "Agent", chat_id: str) -> str:
    checks: list[str] = ["🩺 **SAO 系统自检**", ""]

    # 1. LLM connectivity
    factory = agent.factory
    for model_name in factory.available_models():
        try:
            provider = factory.get_provider(model_name)
            if provider is None:
                checks.append(f"  ⚠️ {model_name}: 未找到 provider")
                continue
            reply = await provider.chat(
                [{"role": "user", "content": "hi"}],
                temperature=0.1,
                max_tokens=5,
            )
            checks.append(f"  ✅ {model_name}: 连通正常")
        except Exception as e:
            checks.append(f"  ❌ {model_name}: {type(e).__name__} — {e}")

    if not factory.available_models():
        checks.append("  ❌ 无 LLM provider 配置")

    checks.append("")

    # 2. Feishu token
    try:
        from app.utils.feishu import get_tenant_access_token
        token = await get_tenant_access_token()
        if token:
            checks.append("  ✅ 飞书 Token: 有效")
        else:
            checks.append("  ❌ 飞书 Token: 获取失败")
    except Exception as e:
        checks.append(f"  ❌ 飞书 Token: {e}")

    # 3. Bitable
    try:
        from app.utils.config import get_settings
        settings = get_settings()
        if settings.feishu_bitable_app_token and settings.feishu_bitable_reminder_table_id:
            from app.skills.reminder.bitable import fetch_pending
            pending = await fetch_pending()
            checks.append(f"  ✅ Bitable: 可访问 ({len(pending)} 条待执行记录)")
        else:
            checks.append("  ⚠️ Bitable: 未配置")
    except Exception as e:
        checks.append(f"  ❌ Bitable: {e}")

    # 4. Scheduler
    try:
        from app.skills.reminder.scheduler import get_scheduler
        scheduler = get_scheduler()
        jobs = scheduler.get_jobs()
        checks.append(f"  ✅ 调度器: 运行中 ({len(jobs)} 个任务)")
    except Exception as e:
        checks.append(f"  ❌ 调度器: {e}")

    return "\n".join(checks)


@slash_command("/memory", "查看长期记忆")
async def cmd_memory(args: str, agent: "Agent", chat_id: str) -> str:
    from app.core.memory import get_memory_stats, list_all_memories

    stats = get_memory_stats()
    memories = list_all_memories(limit=20)

    if not memories:
        return "🧠 还没有长期记忆。\n你可以告诉我“记住xxx”来添加记忆。"

    lines = ["🧠 **长期记忆**", ""]

    # Category emoji mapping
    emoji_map = {"preference": "💡", "fact": "📌", "decision": "🎯", "context": "📝"}

    for mem in memories:
        emoji = emoji_map.get(mem.category, "📝")
        date_str = mem.created_at.strftime("%m-%d %H:%M")
        lines.append(f"  {emoji} [{date_str}] {mem.content}")

    lines.append("")
    lines.append(f"共 {stats['total_memories']} 条记忆 | 分类: {stats['categories']}")
    return "\n".join(lines)


@slash_command("/compact", "压缩对话历史（省 token）")
async def cmd_compact(args: str, agent: "Agent", chat_id: str) -> str:
    from app.core.memory import compact_session

    summary = await compact_session(chat_id, agent.factory)

    # Also clear in-memory history
    agent.histories[chat_id] = []

    return f"📦 对话已压缩。\n\n**摘要**：{summary}"
