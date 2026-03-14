import discord
from discord.ext import commands, tasks
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo
import os

TOKEN = os.getenv("DISCORD_TOKEN")

KST = ZoneInfo("Asia/Seoul")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

DB_FILE = "study_logs.db"

active_sessions = {}
last_log = None
last_deleted_log = None
last_reset_backup = None

study_alerts = {}
rest_alerts = {}

TRACK_CHANNELS = {
    "📖 열공": "study",
    "☘️ 휴식": "rest"
}

TEXT_CHANNEL_NAME = "채팅"
TARGET_USERS = ["멜마", "우디"]


# -------------------------
# DB
# -------------------------

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS study_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user TEXT,
        channel TEXT,
        duration INTEGER,
        date TEXT
    )
    """)

    conn.commit()
    conn.close()


# -------------------------
# util
# -------------------------

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

    if guild.system_channel:
        return guild.system_channel

    return guild.text_channels[0]


# -------------------------
# DB 저장
# -------------------------

def save_log(user, channel, seconds, date):

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    INSERT INTO study_logs (user, channel, duration, date)
    VALUES (?, ?, ?, ?)
    """, (user, channel, seconds, date))

    conn.commit()
    conn.close()


# -------------------------
# 누적 시간 계산
# -------------------------

def get_total_time(user, channel):

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    SELECT SUM(duration)
    FROM study_logs
    WHERE user=? AND channel=?
    """, (user, channel))

    total = cur.fetchone()[0] or 0
    conn.close()

    for session in active_sessions.values():
        if session["name"] == user and session["channel"] == channel:
            total += int((now() - session["start"]).total_seconds())

    return total


# -------------------------
# 자정 기준 시간
# -------------------------

def get_today_time(user, channel):

    today = now().date().isoformat()

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    SELECT SUM(duration)
    FROM study_logs
    WHERE user=? AND channel=? AND date=?
    """, (user, channel, today))

    total = cur.fetchone()[0] or 0
    conn.close()

    for session in active_sessions.values():

        if session["name"] == user and session["channel"] == channel:

            start = session["start"]
            midnight = datetime.combine(now().date(), datetime.min.time(), tzinfo=KST)

            if start < midnight:
                total += int((now() - midnight).total_seconds())
            else:
                total += int((now() - start).total_seconds())

    return total

async def check_study_milestone(member):

    user = member.display_name
    study = get_today_time(user, "📖 열공")

    guild = member.guild
    ch = get_text_channel(guild)

    if study >= 3600 and study_alerts.get(user) != 1:
        study_alerts[user] = 1
        await ch.send(
        f"""{user} 1시간 집중 성곰! 🐻✨\n조금 더 힘내라 곰!"""
        )

    if study >= 14400 and study_alerts.get(user) != 4:
        study_alerts[user] = 4
        await ch.send(
        f"""{user} 4시간 집중 성곰!! 🐻⭐\n이대로 8시간 가쟈 곰..!!"""
        )

    if study >= 28800 and study_alerts.get(user) != 8:
        study_alerts[user] = 8
        await ch.send(
        f"""{user} 8시간 집중 성곰!!! 🐻❤️\n해냈다!! 오늘의 {user}는 엄청나다 곰..!!!"""
        )

async def check_rest(member):

    user = member.display_name
    rest = get_today_time(user, "☘️ 휴식")

    guild = member.guild
    ch = get_text_channel(guild)

    if rest >= 3600 and not rest_alerts.get(user):

        rest_alerts[user] = True

        await ch.send(
        f"""{user}… 휴식이 너무 길다 곰. 🐻⌛\n슬슬 돌아올 시간이다 곰!"""
        )

# -------------------------
# 세션 종료
# -------------------------

async def end_session(member):

    global last_log

    session = active_sessions.pop(member.id, None)

    if not session:
        return

    duration = int((now() - session["start"]).total_seconds())

    if duration <= 0:
        return

    user = session["name"]
    channel = session["channel"]
    date = session["start"].date().isoformat()

    save_log(user, channel, duration, date)

    last_log = (user, channel, duration, date)

    total = get_total_time(user, channel)

    guild = member.guild
    ch = get_text_channel(guild)

    await ch.send(
        f"{user} {channel} {format_time(duration)} 기록 (누적: {format_time(total)})"
    )


# -------------------------
# 음성채널 이벤트
# -------------------------

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
            "name": member.display_name
        }
        
        if after_name == "📖 열공":
            guild = member.guild
            ch = get_text_channel(guild)
            
            await ch.send(
            f"""{member.display_name} 공부 시작! 📚\n오늘 목표까지 달려보자 곰! 🐻🔥"""
            )

    elif before_name in TRACK_CHANNELS and after_name in TRACK_CHANNELS:

        await end_session(member)

        active_sessions[member.id] = {
            "start": now(),
            "channel": after_name,
            "name": member.display_name
        }

    elif before_name in TRACK_CHANNELS and after_name not in TRACK_CHANNELS:

        await end_session(member)

# -------------------------
# 명령어
# -------------------------

@bot.command()
async def 지금(ctx):

    msg = []

    for name in TARGET_USERS:

        study = get_today_time(name, "📖 열공")
        rest = get_today_time(name, "☘️ 휴식")

        msg.append(
            f"{name} | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"
        )

    await ctx.send("\n".join(msg))

@bot.command()
async def 멜마(ctx):

    study = get_today_time("멜마", "📖 열공")
    rest = get_today_time("멜마", "☘️ 휴식")

    await ctx.send(
f"""멜마
| 📖 열공 {format_time(study)} |
| ☘️ 휴식 {format_time(rest)} |"""
    )

@bot.command()
async def 우디(ctx):

    study = get_today_time("우디", "📖 열공")
    rest = get_today_time("우디", "☘️ 휴식")

    await ctx.send(
f"""우디
| 📖 열공 {format_time(study)} |
| ☘️ 휴식 {format_time(rest)} |"""
    )

# -------------------------
# 기록 삭제
# -------------------------

@bot.command()
async def 삭제(ctx):

    global last_log, last_deleted_log

    if last_log is None:
        await ctx.send("삭제할 기록이 없습니다.")
        return

    user, channel, duration, date = last_log

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("""
    SELECT id
    FROM study_logs
    WHERE user=? AND channel=? AND duration=? AND date=?
    ORDER BY id DESC
    LIMIT 1
    """, (user, channel, duration, date))

    row = cur.fetchone()

    if row is None:
        conn.close()
        await ctx.send("삭제할 기록을 찾지 못했습니다.")
        return

    log_id = row[0]

    cur.execute("DELETE FROM study_logs WHERE id=?", (log_id,))
    conn.commit()
    conn.close()

    last_deleted_log = last_log
    last_log = None

    total = get_total_time(user, channel)

    await ctx.send(
f"""기록 삭제 완료
↩️ {user} {channel} {format_time(total)} 기록 (누적: {format_time(total)})"""
    )

# -------------------------
# 기록 복구
# -------------------------

@bot.command()
async def 복구(ctx):

    global last_deleted_log, last_log

    if last_deleted_log is None:
        await ctx.send("복구할 기록이 없습니다.")
        return

    user, channel, duration, date = last_deleted_log

    save_log(user, channel, duration, date)

    last_log = last_deleted_log
    last_deleted_log = None

    total = get_total_time(user, channel)

    await ctx.send(
f"""기록 복구 완료
↪️ {user} {channel} {format_time(duration)} 기록 (누적: {format_time(total)})"""
    )

# -------------------------
# 초기화
# -------------------------

@bot.command()
async def 초기화(ctx):

    global last_reset_backup

    today = now().date().isoformat()

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute(
        "SELECT user, channel, duration, date FROM study_logs WHERE date=?",
        (today,)
    )

    last_reset_backup = cur.fetchall()

    cur.execute("DELETE FROM study_logs WHERE date=?", (today,))

    conn.commit()
    conn.close()

    await ctx.send("⚠️ 오늘 전체 기록이 초기화되었습니다.")


@bot.command()
async def 멜마초기화(ctx):

    global last_reset_backup

    today = now().date().isoformat()

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute(
        "SELECT user, channel, duration, date FROM study_logs WHERE user=? AND date=?",
        ("멜마", today)
    )

    last_reset_backup = cur.fetchall()

    cur.execute(
        "DELETE FROM study_logs WHERE user=? AND date=?",
        ("멜마", today)
    )

    conn.commit()
    conn.close()

    await ctx.send("멜마 오늘 기록이 초기화되었습니다.")


@bot.command()
async def 우디초기화(ctx):

    global last_reset_backup

    today = now().date().isoformat()

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute(
        "SELECT user, channel, duration, date FROM study_logs WHERE user=? AND date=?",
        ("우디", today)
    )

    last_reset_backup = cur.fetchall()

    cur.execute(
        "DELETE FROM study_logs WHERE user=? AND date=?",
        ("우디", today)
    )

    conn.commit()
    conn.close()

    await ctx.send("우디 오늘 기록이 초기화되었습니다.")


# -------------------------
# 초기화 취소
# -------------------------

@bot.command()
async def 초기화취소(ctx):

    global last_reset_backup

    if not last_reset_backup:
        await ctx.send("취소할 초기화 기록이 없습니다.")
        return

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    for row in last_reset_backup:
        cur.execute(
            "INSERT INTO study_logs (user, channel, duration, date) VALUES (?, ?, ?, ?)",
            row
        )

    conn.commit()
    conn.close()

    last_reset_backup = None

    msg = ["♻️ 초기화 취소 완료"]

    for name in TARGET_USERS:

        study = get_today_time(name, "📖 열공")
        rest = get_today_time(name, "☘️ 휴식")

        msg.append(
            f"{name} | 📖 열공 {format_time(study)} | ☘️ 휴식 {format_time(rest)}"
        )

    await ctx.send("\n".join(msg))

# -------------------------
# 자정 랭킹
# -------------------------

@tasks.loop(minutes=1)
async def midnight_ranking():

    n = now()

    if n.hour != 0 or n.minute != 0:
        return

    result = []

    for name in TARGET_USERS:

        study = get_today_time(name, "📖 열공")
        rest = get_today_time(name, "☘️ 휴식")

        result.append((name, study, rest))

    study_rank = sorted(result, key=lambda x: x[1], reverse=True)
    rest_rank = sorted(result, key=lambda x: x[2], reverse=True)

    medals = ["🥇", "🥈"]
    rest_icons = ["🏖️", "🛋️"]

    msg = []

    msg.append("═══ 오늘의 공부 랭킹 ═══ \n")

    for i, (name, study, _) in enumerate(study_rank):
        msg.append(f"{medals[i]} {name} : {format_time(study)}")

    msg.append("\n══════════════\n")

    msg.append("═══ 오늘의 휴식 랭킹 ═══ \n")

    for i, (name, _, rest) in enumerate(rest_rank):
        msg.append(f"{rest_icons[i]} {name} : {format_time(rest)}")

    msg.append("\n══════════════")
    
    winner = study_rank[0][0]
    
    msg.append(f"\n오늘 공부왕은~~~ 👑{winner}! 축하한다 곰~! 🐻🎉")
    
    guild = bot.guilds[0]
    ch = get_text_channel(guild)
    
    await ch.send("\n".join(msg))

@tasks.loop(minutes=1)
async def night_message():
    n = now()

    if n.hour != 23 or n.minute != 0:
        return

    guild = bot.guilds[0]
    ch = get_text_channel(guild)

    await ch.send(
    """오늘도 공부 수고했다 곰!\n이제 푹 쉬어라 곰 :) 🐻🌙"""
    )

@tasks.loop(minutes=1)
async def study_checker():

    for member in bot.guilds[0].members:

        if member.bot:
            continue

        if member.display_name not in TARGET_USERS:
            continue

        await check_study_milestone(member)
        await check_rest(member)

# -------------------------
# 봇 시작
# -------------------------

@bot.event
async def on_ready():

    print("Study Bot 실행됨")

    init_db()

    midnight_ranking.start()
    study_checker.start()
    night_message.start()

bot.run(TOKEN)