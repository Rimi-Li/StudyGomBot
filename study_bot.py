import os
import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import discord
from discord.ext import commands, tasks

# -----------------------------
# 설정
# -----------------------------

TOKEN = os.getenv("DISCORD_TOKEN")

KST = ZoneInfo("Asia/Seoul")

DB_FILE = "study_logs.db"

TEXT_CHANNEL_NAME = "채팅"

TRACK_CHANNELS = {
    "📖 열공": "study",
    "☘️ 휴식": "rest"
}

TARGET_USERS = ["멜마", "배키"]

# 디스코드 설정
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# 런타임 상태
active_sessions = {}

last_log = None
last_deleted_log = None
last_reset_backup = None

study_alerts = {
    "멜마": set(),
    "배키": set()
}

rest_tasks = {}
study_tasks = {}

last_night_message = None

# 유틸
async def check_study_milestone(member):

    user = member.display_name
    alerts = study_alerts.setdefault(user, set())
    user_id = member.id

    study = get_time(user_id, "📖 열공", True)

    ch = get_text_channel(member.guild)

    if study >= 3600 and 1 not in alerts:
        alerts.add(1)

        await ch.send(
            f"""{user} 1시간 집중 성곰! 🐻✨
조금 더 힘내라 곰!"""
        )

    if study >= 14400 and 4 not in alerts:
        alerts.add(4)

        await ch.send(
            f"""{user} 4시간 집중 성곰!! 🐻⭐
이대로 8시간 가쟈 곰..!!"""
        )

    if study >= 28800 and 8 not in alerts:
        alerts.add(8)

        await ch.send(
            f"""{user} 8시간 집중 성곰!!! 🐻❤️
해냈다!! 오늘의 {user}는 엄청나다 곰..!!!"""
        )

async def study_timer(member):

    while member.id in active_sessions:

        session = active_sessions.get(member.id)

        if not session or session["channel"] != "📖 열공":
            return

        await check_study_milestone(member)

        await discord.utils.sleep_until(now() + timedelta(minutes=1))

async def rest_warning(member):

    await discord.utils.sleep_until(now() + timedelta(hours=1))

    if member.id in active_sessions:

        session = active_sessions[member.id]

        if session["channel"] == "☘️ 휴식":

            ch = get_text_channel(member.guild)

            await ch.send(
                f"""{member.display_name}… 휴식이 너무 길다 곰. 🐻⌛
슬슬 돌아올 시간이다 곰!"""
            )

def now():
    return datetime.now(KST)


def format_time(sec):

    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60

    if h == 0 and m == 0:
        return f"{s:02d}초"

    if h == 0:
        return f"{m:02d}분 {s:02d}초"

    return f"{h}시간 {m:02d}분 {s:02d}초"


def get_text_channel(guild):

    for ch in guild.text_channels:
        if ch.name == TEXT_CHANNEL_NAME:
            return ch

    return guild.system_channel or guild.text_channels[0]

# DB
def db_execute(query, params=(), fetch=False):

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute(query, params)

    result = None

    if fetch:
        result = cur.fetchall()

    conn.commit()
    conn.close()

    return result


def init_db():

    db_execute("""
    CREATE TABLE IF NOT EXISTS study_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        user_name TEXT,
        channel TEXT,
        duration INTEGER,
        date TEXT
    )
    """)

# 시간 계산
def get_time(user_id, channel, today_only=False):

    query = """
    SELECT SUM(duration)
    FROM study_logs
    WHERE user_id=? AND channel=?
    """

    params = [user_id, channel]

    if today_only:
        query += " AND date=?"
        params.append(now().date().isoformat())

    rows = db_execute(query, params, True)

    total = rows[0][0] or 0

    for session in active_sessions.values():

        if session["user_id"] == user_id and session["channel"] == channel:
            total += int((now() - session["start"]).total_seconds())

    return total

# 기록 저장
def save_log(user_id, user_name, channel, seconds, date):

    db_execute("""
    INSERT INTO study_logs (user_id, user_name, channel, duration, date)
    VALUES (?, ?, ?, ?, ?)
    """, (user_id, user_name, channel, seconds, date))

# 세션 종료
async def end_session(member):

    global last_log

    session = active_sessions.pop(member.id, None)

    if not session:
        return

    duration = int((now() - session["start"]).total_seconds())

    if duration <= 0:
        return

    user_id = session["user_id"]
    user_name = session["name"]
    channel = session["channel"]
    date = session["start"].date().isoformat()

    save_log(user_id, user_name, channel, duration, date)

    last_log = (user_id, user_name, channel, duration, date)

    total = get_time(user_id, channel)

    ch = get_text_channel(member.guild)

    await ch.send(
        f"{user_name} {channel} {format_time(duration)} 기록 (누적: {format_time(total)})"
    )

# 음성 이벤트
@bot.event
async def on_voice_state_update(member, before, after):

    if member.bot:
        return

    before_name = before.channel.name if before.channel else None
    after_name = after.channel.name if after.channel else None

    if before_name not in TRACK_CHANNELS and after_name in TRACK_CHANNELS:

        active_sessions[member.id] = {
            "start": now(),
            "channel": after_name,
            "name": member.display_name,
            "user_id": member.id
        }

        if after_name == "📖 열공":

            ch = get_text_channel(member.guild)

            await ch.send(
                f"{member.display_name} 공부 시작! 📚\n오늘 목표까지 달려보자 곰! 🐻🔥"
            )

            await check_study_milestone(member)

            task = bot.loop.create_task(study_timer(member))
            study_tasks[member.id] = task
            
        if after_name == "☘️ 휴식":

            task = bot.loop.create_task(rest_warning(member))
            rest_tasks[member.id] = task


    elif before_name in TRACK_CHANNELS and after_name in TRACK_CHANNELS:

        if member.id in study_tasks:
            study_tasks[member.id].cancel()
            del study_tasks[member.id]

        if member.id in rest_tasks:
            rest_tasks[member.id].cancel()
            del rest_tasks[member.id]

        await end_session(member)

        active_sessions[member.id] = {
            "start": now(),
            "channel": after_name,
            "name": member.display_name,
            "user_id": member.id
        }

        if after_name == "📖 열공":
            ch = get_text_channel(member.guild)

            await ch.send(
                f"{member.display_name} 공부 시작! 📚\n오늘 목표까지 달려보자 곰! 🐻🔥"
            )

            await check_study_milestone(member)

            task = bot.loop.create_task(study_timer(member))
            study_tasks[member.id] = task

        if after_name == "☘️ 휴식":
            task = bot.loop.create_task(rest_warning(member))
            rest_tasks[member.id] = task

    elif before_name in TRACK_CHANNELS and after_name not in TRACK_CHANNELS:
        #스터디 타이머 취소
        if member.id in study_tasks:
            study_tasks[member.id].cancel()
            del study_tasks[member.id]

        #휴식 타이머 취소
        if member.id in rest_tasks:
            rest_tasks[member.id].cancel()
            del rest_tasks[member.id]

        await end_session(member)

# 조회 명령어
async def send_user_time(ctx, user_name):

    guild = ctx.guild

    member = discord.utils.get(guild.members, display_name=user_name)

    if not member:
        await ctx.send("사용자를 찾지 못했습니다.")
        return

    study = get_time(member.id, "📖 열공", True)
    rest = get_time(member.id, "☘️ 휴식", True)

    await ctx.send(
f"""{user_name}
| 📖 열공 {format_time(study)} |
| ☘️ 휴식 {format_time(rest)} |"""
    )


@bot.command()
async def 지금(ctx):

    lines = []

    for name in TARGET_USERS:

        member = discord.utils.get(ctx.guild.members, display_name=name)

        if not member:
            continue

        study = get_time(member.id, "📖 열공", True)
        rest = get_time(member.id, "☘️ 휴식", True)

        lines.append(
            f"{name} | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"
        )

    await ctx.send("\n".join(lines))


@bot.command()
async def 멜마(ctx):
    await send_user_time(ctx, "멜마")


@bot.command()
async def 배키(ctx):
    await send_user_time(ctx, "배키")

# 삭제 / 복구
@bot.command()
async def 삭제(ctx):

    global last_log, last_deleted_log

    if not last_log:
        await ctx.send("삭제할 기록이 없다 곰")
        return

    user_id, user_name, channel, duration, date = last_log

    rows = db_execute("""
    SELECT id
    FROM study_logs
    WHERE user_id=? AND channel=? AND duration=? AND date=?
    ORDER BY id DESC
    LIMIT 1
    """, (user_id, channel, duration, date), True)

    if not rows:
        await ctx.send("삭제할 기록을 찾지 못했다 곰")
        return

    log_id = rows[0][0]

    db_execute("DELETE FROM study_logs WHERE id=?", (log_id,))

    last_deleted_log = last_log
    last_log = None

    total = get_time(user_id, channel)

    await ctx.send(
        f"⛔기록 삭제 완료\n>> {user_name} {channel} {format_time(total)} (누적)"
    )


@bot.command()
async def 복구(ctx):

    global last_deleted_log, last_log

    if not last_deleted_log:
        await ctx.send("복구할 기록이 없다 곰")
        return

    user_id, user_name, channel, duration, date = last_deleted_log

    save_log(user_id, user_name, channel, duration, date)

    last_log = last_deleted_log
    last_deleted_log = None

    total = get_time(user_id, channel)

    await ctx.send(
        f"♻️기록 복구 완료\n>> {user_name} {channel} {format_time(duration)} (누적 {format_time(total)})"
    )

# 초기화
@bot.command()
async def 멜마초기화(ctx):

    global last_reset_backup

    member = discord.utils.get(ctx.guild.members, display_name="멜마")
    if not member:
        await ctx.send("멜마 사용자를 찾지 못했다 곰")
        return

    today = now().date().isoformat()

    last_reset_backup = db_execute(
        "SELECT user_id,user_name,channel,duration,date FROM study_logs WHERE user_id=? AND date=?",
        (member.id, today), True
    )

    db_execute(
        "DELETE FROM study_logs WHERE user_id=? AND date=?",
        (member.id, today)
    )

    study = get_time(member.id, "📖 열공", True)
    rest = get_time(member.id, "☘️ 휴식", True)

    await ctx.send(
        f"""⚠️ 멜마 오늘 기록이 초기화되었다 곰
멜마 | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"""
    )


@bot.command()
async def 배키초기화(ctx):

    global last_reset_backup

    member = discord.utils.get(ctx.guild.members, display_name="배키")
    if not member:
        await ctx.send("배키 사용자를 찾지 못했다 곰")
        return

    today = now().date().isoformat()

    last_reset_backup = db_execute(
        "SELECT user_id,user_name,channel,duration,date FROM study_logs WHERE user_id=? AND date=?",
        (member.id, today), True
    )

    db_execute(
        "DELETE FROM study_logs WHERE user_id=? AND date=?",
        (member.id, today)
    )

    study = get_time(member.id, "📖 열공", True)
    rest = get_time(member.id, "☘️ 휴식", True)

    await ctx.send(
        f"""⚠️ 배키 오늘 기록이 초기화되었다 곰
배키 | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"""
    )

@bot.command()
async def 초기화(ctx):

    global last_reset_backup

    today = now().date().isoformat()

    last_reset_backup = db_execute(
        "SELECT user_id,user_name,channel,duration,date FROM study_logs WHERE date=?",
        (today,), True
    )

    db_execute("DELETE FROM study_logs WHERE date=?", (today,))

    msg = ["⚠️ 오늘 전체 기록이 초기화되었다 곰"]

    for name in TARGET_USERS:

        member = discord.utils.get(ctx.guild.members, display_name=name)

        if not member:
            continue

        study = get_time(member.id, "📖 열공", True)
        rest = get_time(member.id, "☘️ 휴식", True)

        msg.append(
            f"{name} | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"
        )

    await ctx.send("\n".join(msg))


@bot.command()
async def 초기화취소(ctx):

    global last_reset_backup

    if not last_reset_backup:
        await ctx.send("취소할 초기화 기록이 없다 곰")
        return

    for row in last_reset_backup:

        db_execute("""
        INSERT INTO study_logs (user_id,user_name,channel,duration,date)
        VALUES (?,?,?,?,?)
        """, row)

    last_reset_backup = None

    msg = ["♻️ 초기화가 취소되었다 곰"]

    for name in TARGET_USERS:

        member = discord.utils.get(ctx.guild.members, display_name=name)

        if not member:
            continue

        study = get_time(member.id, "📖 열공", True)
        rest = get_time(member.id, "☘️ 휴식", True)

        msg.append(
            f"{name} | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"
        )

    await ctx.send("\n".join(msg))

# 11PM 스터디 종료
@tasks.loop(minutes=1)
async def night_message():

    global last_night_message

    n = now()

    if n.hour != 23 or n.minute != 0:
        return

    if last_night_message == n.date():
        return

    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    ch = get_text_channel(guild)

    await ch.send(
"""오늘도 공부 수고했다 곰!
이제 푹 쉬어라 곰 :) 🐻🌙"""
    )

    last_night_message = n.date()

# 자정 랭킹
@tasks.loop(minutes=1)
async def midnight_ranking():

    n = now()

    if n.hour != 0 or n.minute != 0:
        return

    guild = bot.guilds[0] if bot.guilds else None
    if not guild:
        return

    result = []

    for name in TARGET_USERS:

        member = discord.utils.get(guild.members, display_name=name)

        if not member:
            continue

        study = get_time(member.id, "📖 열공", True)
        rest = get_time(member.id, "☘️ 휴식", True)

        result.append((name, study, rest))

    study_rank = sorted(result, key=lambda x: x[1], reverse=True)
    rest_rank = sorted(result, key=lambda x: x[2], reverse=True)

    medals = ["🥇", "🥈"]
    rest_icons = ["🐢", "🦥"]

    msg = []

    msg.append("═══ 오늘의 공부 랭킹 ═══\n")

    for i, (name, study, _) in enumerate(study_rank):
        msg.append(f"{medals[i]} {name} : {format_time(study)}")

    msg.append("\n══════════════\n")
    msg.append("═══ 오늘의 휴식 랭킹 ═══\n")

    for i, (name, _, rest) in enumerate(rest_rank):
        msg.append(f"{rest_icons[i]} {name} : {format_time(rest)}")

    msg.append("\n══════════════")

    winner = study_rank[0][0]

    msg.append(f"\n오늘 공부왕은~~~ 👑{winner}! 축하한다 곰~! 🐻🎉")

    ch = get_text_channel(guild)

    await ch.send("\n".join(msg))

    study_alerts.clear()

# 봇 시작
@bot.event
async def on_ready():

    print("Study Bot 실행됨")

    init_db()

    if not midnight_ranking.is_running():
        midnight_ranking.start()

    if not night_message.is_running():
        night_message.start()


bot.run(TOKEN)