import discord
from discord import app_commands
from discord.ext import commands, tasks
import random
import asyncio
from datetime import datetime, timedelta
import pytz
import json
import os
import sys
from pathlib import Path
import threading
import time

# Get bot token from environment variable
BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
if not BOT_TOKEN:
    print("Error: DISCORD_BOT_TOKEN environment variable not set!")
    sys.exit(1)

# Configuration from environment
TIMEZONE = pytz.timezone(os.getenv('TIMEZONE', 'Asia/Taipei'))
DATA_DIR = Path(os.getenv('DATA_DIR', './data'))
SAVE_INTERVAL = int(os.getenv('SAVE_INTERVAL', '60'))  # Save every 60 seconds

# Ensure data directory exists
DATA_DIR.mkdir(exist_ok=True)
DRAWS_FILE = DATA_DIR / 'lucky_draws.json'
BACKUP_FILE = DATA_DIR / 'lucky_draws.backup.json'

# 機器人設定
intents = discord.Intents.default()
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

# 儲存抽獎資訊
lucky_draws = {}
last_draw_id = 0
save_lock = threading.Lock()

def save_draws_to_file():
    """儲存抽獎資料到檔案"""
    with save_lock:
        try:
            # Prepare data for JSON serialization
            save_data = {
                'last_draw_id': last_draw_id,
                'draws': {}
            }

            for draw_id, draw in lucky_draws.items():
                # Convert set to list for JSON
                draw_copy = draw.copy()
                draw_copy['participants'] = list(draw_copy['participants'])
                # Convert datetime to ISO format
                draw_copy['end_time'] = draw_copy['end_time'].isoformat()

                # 確保新舊版本相容
                if 'winner_ids' not in draw_copy:
                    draw_copy['winner_ids'] = [draw_copy.get('winner_id')] if draw_copy.get('winner_id') else []
                if 'winners_count' not in draw_copy:
                    draw_copy['winners_count'] = 1

                save_data['draws'][str(draw_id)] = draw_copy

            # Save to temporary file first
            temp_file = DRAWS_FILE.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)

            # Backup current file if exists
            if DRAWS_FILE.exists():
                DRAWS_FILE.replace(BACKUP_FILE)

            # Move temp file to actual file
            temp_file.replace(DRAWS_FILE)

            print(f"[{datetime.now()}] 已儲存 {len(lucky_draws)} 個抽獎資料")

        except Exception as e:
            print(f"儲存資料時發生錯誤: {e}")

def load_draws_from_file():
    """從檔案載入抽獎資料"""
    global lucky_draws, last_draw_id

    if not DRAWS_FILE.exists():
        print("沒有找到儲存的抽獎資料")
        return

    try:
        with open(DRAWS_FILE, 'r', encoding='utf-8') as f:
            save_data = json.load(f)

        last_draw_id = save_data.get('last_draw_id', 0)

        for draw_id_str, draw_data in save_data.get('draws', {}).items():
            draw_id = int(draw_id_str)
            # Convert list back to set
            draw_data['participants'] = set(draw_data['participants'])
            # Convert ISO format back to datetime
            draw_data['end_time'] = datetime.fromisoformat(draw_data['end_time'])
            # Ensure timezone awareness
            if draw_data['end_time'].tzinfo is None:
                draw_data['end_time'] = TIMEZONE.localize(draw_data['end_time'])

            # 處理舊版本相容性
            if 'winner_ids' not in draw_data:
                draw_data['winner_ids'] = [draw_data.get('winner_id')] if draw_data.get('winner_id') else []
            if 'winners_count' not in draw_data:
                draw_data['winners_count'] = 1

            lucky_draws[draw_id] = draw_data

        print(f"已載入 {len(lucky_draws)} 個抽獎資料")

    except Exception as e:
        print(f"載入資料時發生錯誤: {e}")
        # Try to load backup
        if BACKUP_FILE.exists():
            print("嘗試載入備份檔案...")
            try:
                BACKUP_FILE.replace(DRAWS_FILE)
                load_draws_from_file()
            except:
                print("備份檔案也無法載入")

class LuckyDrawView(discord.ui.View):
    def __init__(self, draw_id):
        super().__init__(timeout=None)
        self.draw_id = draw_id

    @discord.ui.button(label='參加', style=discord.ButtonStyle.primary, emoji='🎉')
    async def participate(self, interaction: discord.Interaction, button: discord.ui.Button):
        draw = lucky_draws.get(self.draw_id)
        if not draw:
            await interaction.response.send_message('此抽獎活動已結束或不存在！', ephemeral=True)
            return

        user_id = interaction.user.id

        if user_id in draw['participants']:
            await interaction.response.send_message('你已經參加過這個抽獎了！', ephemeral=True)
        elif user_id == draw['creator_id']:
            await interaction.response.send_message('創建者沒辦法參與抽獎！', ephemeral=True)
        else:
            draw['participants'].add(user_id)
            await interaction.response.send_message(
                f'✅ 成功參加抽獎！目前參加人數：{len(draw["participants"])}',
                ephemeral=True
            )

            # 更新嵌入訊息
            embed = create_draw_embed(draw)
            await interaction.message.edit(embed=embed)

            # 標記需要儲存
            schedule_save()

def create_draw_embed(draw):
    """創建抽獎嵌入訊息"""
    embed = discord.Embed(
        title="🎉 抽獎活動 🎉",
        description=f"獎品：**{draw['prize']}**",
        color=discord.Color.gold()
    )

    # 計算剩餘時間
    time_left = draw['end_time'] - datetime.now(TIMEZONE)
    minutes_left = max(0, int(time_left.total_seconds() / 60))

    embed.add_field(
        name="⏰ 結束時間",
        value=draw['end_time'].strftime("%Y-%m-%d %H:%M:%S"),
        inline=True
    )
    embed.add_field(
        name="⏱️ 剩餘時間",
        value=f"{minutes_left} 分鐘",
        inline=True
    )
    embed.add_field(
        name="👥 參加人數",
        value=f"{len(draw['participants'])} 人",
        inline=True
    )

    # 新增：顯示得獎者數量
    winners_count = draw.get('winners_count', 1)
    embed.add_field(
        name="🏆 得獎名額",
        value=f"{winners_count} 人",
        inline=True
    )

    embed.set_footer(text=f"抽獎ID: {draw['id']} | 創建者: {draw['creator_name']}")

    return embed

# 自動儲存排程
save_scheduled = False
def schedule_save():
    global save_scheduled
    save_scheduled = True

@bot.event
async def on_ready():
    print(f'{bot.user} 已上線！')

    # 載入儲存的資料
    load_draws_from_file()

    # 重新啟動未結束的抽獎視圖
    for draw_id, draw in lucky_draws.items():
        if draw['active']:
            # 這裡可以嘗試重新附加視圖到原始訊息
            # 但需要儲存 message_id 和 channel_id
            pass

    try:
        # 同步斜線指令
        synced = await bot.tree.sync()
        print(f'已同步 {len(synced)} 個斜線指令')
    except Exception as e:
        print(f'同步指令時出錯: {e}')

    # 啟動定時任務
    check_draws.start()
    auto_save.start()

@bot.tree.command(name='抽獎', description='創建一個新的抽獎活動')
@app_commands.describe(
    prize='要抽出的獎品名稱（例如：Steam點數1000元）',
    minutes='抽獎持續時間，以分鐘為單位（最少1分鐘，最多10080分鐘=7天）',
    winners='要抽出的得獎者數量（預設1人，最多100人）'
)
async def create_lucky_draw(interaction: discord.Interaction, prize: str, minutes: int, winners: int = 1):
    global last_draw_id

    # 參數驗證
    if minutes < 1:
        await interaction.response.send_message("❌ 持續時間必須至少1分鐘！", ephemeral=True)
        return

    if minutes > 10080:  # 7天
        await interaction.response.send_message("❌ 持續時間不能超過7天（10080分鐘）！", ephemeral=True)
        return

    if winners < 1:
        await interaction.response.send_message("❌ 得獎者數量必須至少1人！", ephemeral=True)
        return

    if winners > 100:
        await interaction.response.send_message("❌ 得獎者數量不能超過100人！", ephemeral=True)
        return

    # 創建抽獎
    last_draw_id += 1
    draw_id = last_draw_id
    end_time = datetime.now(TIMEZONE) + timedelta(minutes=minutes)

    draw = {
        'id': draw_id,
        'prize': prize,
        'end_time': end_time,
        'participants': set(),
        'channel_id': interaction.channel_id,
        'creator_id': interaction.user.id,
        'creator_name': interaction.user.name,
        'active': True,
        'created_at': datetime.now(TIMEZONE).isoformat(),
        'winners_count': winners,  # 新增欄位
        'winner_ids': []  # 改為列表以支援多個得獎者
    }

    lucky_draws[draw_id] = draw

    # 創建嵌入訊息和按鈕
    embed = create_draw_embed(draw)
    view = LuckyDrawView(draw_id)

    # 發送抽獎訊息
    await interaction.response.send_message(
        "@everyone 新的抽獎活動開始了！點擊下方按鈕參加！",
        embed=embed,
        view=view,
        allowed_mentions=discord.AllowedMentions(everyone=True)
    )

    # 儲存 message_id 以便重啟後恢復
    try:
        message = await interaction.original_response()
        draw['message_id'] = message.id
    except:
        pass

    # 排程儲存
    schedule_save()

@bot.tree.command(name='抽獎列表', description='顯示所有進行中的抽獎活動')
async def list_draws(interaction: discord.Interaction):
    active_draws = [draw for draw in lucky_draws.values() if draw['active']]

    if not active_draws:
        embed = discord.Embed(
            title="📋 進行中的抽獎活動",
            description="目前沒有進行中的抽獎活動。",
            color=discord.Color.blue()
        )
        embed.add_field(
            name="💡 提示",
            value="使用 `/抽獎` 指令來創建新的抽獎活動！",
            inline=False
        )
        await interaction.response.send_message(embed=embed)
        return

    embed = discord.Embed(
        title="📋 進行中的抽獎活動",
        description=f"共有 {len(active_draws)} 個進行中的抽獎",
        color=discord.Color.blue()
    )

    for draw in active_draws:
        time_left = draw['end_time'] - datetime.now(TIMEZONE)
        minutes_left = max(0, int(time_left.total_seconds() / 60))
        hours_left = minutes_left // 60
        mins_left = minutes_left % 60

        time_str = f"{hours_left}小時{mins_left}分鐘" if hours_left > 0 else f"{mins_left}分鐘"
        winners_count = draw.get('winners_count', 1)

        embed.add_field(
            name=f"🎁 ID: {draw['id']} - {draw['prize']}",
            value=f"👥 參加人數：{len(draw['participants'])}\n"
                  f"🏆 得獎名額：{winners_count}\n"
                  f"⏰ 剩餘時間：{time_str}\n"
                  f"👤 創建者：{draw['creator_name']}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name='強制結束', description='強制結束指定的抽獎（需要管理員權限）')
@app_commands.describe(
    draw_id='要結束的抽獎ID（可從 /抽獎列表 查看）'
)
@app_commands.default_permissions(administrator=True)
async def force_end(interaction: discord.Interaction, draw_id: int):
    if draw_id not in lucky_draws:
        await interaction.response.send_message("❌ 找不到此抽獎ID！", ephemeral=True)
        return

    if not lucky_draws[draw_id]['active']:
        await interaction.response.send_message("❌ 此抽獎已經結束！", ephemeral=True)
        return

    await interaction.response.send_message(f"⏳ 正在結束抽獎 ID: {draw_id}...")
    await end_draw(draw_id)
    await interaction.edit_original_response(content=f"✅ 已強制結束抽獎 ID: {draw_id}")

@bot.tree.command(name='抽獎紀錄', description='查看最近的抽獎結果')
@app_commands.describe(
    limit='要顯示的紀錄數量（預設5筆，最多20筆）'
)
async def draw_history(interaction: discord.Interaction, limit: int = 5):
    # 限制數量
    limit = min(max(1, limit), 20)

    # 取得已結束的抽獎
    ended_draws = [draw for draw in lucky_draws.values() if not draw['active']]

    if not ended_draws:
        await interaction.response.send_message("目前沒有已結束的抽獎紀錄。", ephemeral=True)
        return

    # 按ID排序（最新的在前）
    ended_draws.sort(key=lambda x: x['id'], reverse=True)
    ended_draws = ended_draws[:limit]

    embed = discord.Embed(
        title="📜 抽獎紀錄",
        description=f"最近 {len(ended_draws)} 筆抽獎結果",
        color=discord.Color.purple()
    )

    for draw in ended_draws:
        winner_text = "無人參加"
        winner_ids = draw.get('winner_ids', [])

        if winner_ids:
            if len(winner_ids) == 1:
                winner_text = f"<@{winner_ids[0]}>"
            else:
                winner_text = f"{len(winner_ids)} 位得獎者"

        winners_count = draw.get('winners_count', 1)

        embed.add_field(
            name=f"ID: {draw['id']} - {draw['prize']}",
            value=f"🏆 得獎者：{winner_text}\n"
                  f"👥 參加人數：{len(draw['participants'])} / 名額：{winners_count}\n"
                  f"📅 結束時間：{draw['end_time'].strftime('%m/%d %H:%M')}",
            inline=False
        )

    await interaction.response.send_message(embed=embed)

@bot.tree.command(name='備份狀態', description='查看資料備份狀態（管理員專用）')
@app_commands.default_permissions(administrator=True)
async def backup_status(interaction: discord.Interaction):
    embed = discord.Embed(
        title="💾 資料備份狀態",
        color=discord.Color.blue()
    )

    # 檢查主要檔案
    if DRAWS_FILE.exists():
        file_stat = DRAWS_FILE.stat()
        file_size = file_stat.st_size / 1024  # KB
        file_time = datetime.fromtimestamp(file_stat.st_mtime)
        embed.add_field(
            name="主要資料檔",
            value=f"大小：{file_size:.2f} KB\n"
                  f"最後修改：{file_time.strftime('%Y-%m-%d %H:%M:%S')}",
            inline=False
        )
    else:
        embed.add_field(name="主要資料檔", value="❌ 不存在", inline=False)

    # 檢查備份檔案
    if BACKUP_FILE.exists():
        backup_stat = BACKUP_FILE.stat()
        backup_size = backup_stat.st_size / 1024  # KB
        backup_time = datetime.fromtimestamp(backup_stat.st_mtime)
        embed.add_field(
            name="備份檔案",
            value=f"大小：{backup_size:.2f} KB\n"
                  f"最後修改：{backup_time.strftime('%Y-%m-%d %H:%M:%S')}",
            inline=False
        )
    else:
        embed.add_field(name="備份檔案", value="❌ 不存在", inline=False)

    # 目前資料統計
    active_count = sum(1 for d in lucky_draws.values() if d['active'])
    ended_count = len(lucky_draws) - active_count

    embed.add_field(
        name="📊 資料統計",
        value=f"總抽獎數：{len(lucky_draws)}\n"
              f"進行中：{active_count}\n"
              f"已結束：{ended_count}",
        inline=False
    )

    embed.set_footer(text=f"資料目錄：{DATA_DIR}")

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name='幫助', description='顯示機器人使用說明')
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🎰 抽獎機器人使用指南",
        description="歡迎使用抽獎機器人！以下是所有可用的斜線指令：",
        color=discord.Color.green()
    )

    embed.add_field(
        name="🎉 `/抽獎`",
        value="創建新的抽獎活動\n"
              "• **prize**: 獎品名稱\n"
              "• **minutes**: 持續時間（分鐘）\n"
              "• **winners**: 得獎者數量（選填，預設1人）",
        inline=False
    )

    embed.add_field(
        name="📋 `/抽獎列表`",
        value="查看所有進行中的抽獎活動",
        inline=False
    )

    embed.add_field(
        name="📜 `/抽獎紀錄`",
        value="查看最近的抽獎結果\n"
              "• **limit**: 顯示數量（選填）",
        inline=False
    )

    embed.add_field(
        name="🛑 `/強制結束`",
        value="強制結束抽獎（管理員專用）\n"
              "• **draw_id**: 抽獎ID",
        inline=False
    )

    embed.add_field(
        name="💾 `/備份狀態`",
        value="查看資料備份狀態（管理員專用）",
        inline=False
    )

    embed.add_field(
        name="💡 使用提示",
        value="• 輸入 `/` 即可看到所有可用指令\n"
              "• 點擊「參加」按鈕參與抽獎\n"
              "• 每人每個抽獎只能參加一次\n"
              "• 可設定多位得獎者（預設1位）\n"
              "• 時間到達後會自動開獎\n"
              "• 資料會定期自動儲存",
        inline=False
    )

    embed.set_footer(text="祝你好運！🍀")

    await interaction.response.send_message(embed=embed, ephemeral=True)

@tasks.loop(seconds=30)
async def check_draws():
    """定期檢查並結束已到期的抽獎"""
    current_time = datetime.now(TIMEZONE)
    draws_to_end = []

    for draw_id, draw in lucky_draws.items():
        if draw['active'] and current_time >= draw['end_time']:
            draws_to_end.append(draw_id)

    for draw_id in draws_to_end:
        await end_draw(draw_id)

@tasks.loop(seconds=SAVE_INTERVAL)
async def auto_save():
    """定期自動儲存資料"""
    global save_scheduled
    if save_scheduled:
        save_draws_to_file()
        save_scheduled = False

async def end_draw(draw_id):
    """結束抽獎並抽出得獎者"""
    draw = lucky_draws.get(draw_id)
    if not draw or not draw['active']:
        return

    draw['active'] = False
    draw['ended_at'] = datetime.now(TIMEZONE).isoformat()
    channel = bot.get_channel(draw['channel_id'])

    if not channel:
        return

    # 準備結果嵌入訊息
    result_embed = discord.Embed(
        title="🎊 抽獎結果公布 🎊",
        description=f"獎品：**{draw['prize']}**",
        color=discord.Color.green()
    )

    winners_count = draw.get('winners_count', 1)

    if len(draw['participants']) == 0:
        result_embed.add_field(
            name="😢 結果",
            value="沒有人參加這次抽獎",
            inline=False
        )
        draw['winner_ids'] = []
    else:
        # 計算實際可抽出的得獎者數量
        actual_winners_count = min(winners_count, len(draw['participants']))

        # 隨機抽出得獎者
        winner_ids = random.sample(list(draw['participants']), actual_winners_count)
        draw['winner_ids'] = winner_ids

        # 相容舊版本
        draw['winner_id'] = winner_ids[0] if winner_ids else None

        # 顯示所有得獎者
        winners_text = "\n".join([f"{i+1}. <@{winner_id}>" for i, winner_id in enumerate(winner_ids)])

        result_embed.add_field(
            name=f"🏆 恭喜得獎者（共 {actual_winners_count} 位）",
            value=winners_text,
            inline=False
        )
        result_embed.add_field(
            name="🎉 獲得獎品",
            value=f"**{draw['prize']}**",
            inline=False
        )

        if actual_winners_count < winners_count:
            result_embed.add_field(
                name="⚠️ 注意",
                value=f"原定抽 {winners_count} 位得獎者，但只有 {len(draw['participants'])} 人參加",
                inline=False
            )

    result_embed.add_field(
        name="📊 統計資訊",
        value=f"• 參加人數：{len(draw['participants'])} 人\n"
              f"• 得獎名額：{winners_count} 人\n"
              f"• 抽獎ID：{draw_id}\n"
              f"• 創建者：{draw['creator_name']}",
        inline=False
    )

    result_embed.set_footer(
        text=f"結束時間：{datetime.now(TIMEZONE).strftime('%Y-%m-%d %H:%M:%S')}"
    )

    # 發送結果
    await channel.send(
        content="@everyone 抽獎結果出爐！" if draw.get('winner_ids') else "抽獎已結束",
        embed=result_embed,
        allowed_mentions=discord.AllowedMentions(everyone=True)
    )

    # 排程儲存
    schedule_save()

# 優雅關閉處理
def shutdown_handler():
    print("\n正在儲存資料並關閉...")
    save_draws_to_file()

import signal
signal.signal(signal.SIGTERM, lambda s, f: shutdown_handler())
signal.signal(signal.SIGINT, lambda s, f: shutdown_handler())

# 執行機器人
if __name__ == "__main__":
    bot.run(BOT_TOKEN)

