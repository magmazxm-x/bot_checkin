import discord
from discord.ext import commands, tasks
from discord import app_commands
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime, re, pytz, asyncio, io, time, json, os
import pytesseract
from PIL import Image, ImageEnhance
from flask import Flask
from threading import Thread

# --- [1] ระบบ Web Server สำหรับ Keep Alive บน Render ---
app = Flask('')

@app.route('/')
def home():
    return "Police Bot is Online!"

def run_flask():
    # ดึง Port จาก Render (Default 8080)
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# --- [2] ข้อมูลพื้นฐานและการดึงค่าความลับ ---
# ดึง TOKEN และ Google Sheets Credentials จาก Environment Variables
TOKEN = os.getenv('DISCORD_TOKEN')
GOOGLE_CREDS = os.getenv('GOOGLE_CREDS_JSON')

REG_CHANNEL_ID = 1495618190418378852
NOTI_CHANNEL_ID = 1495617510974816316
UPDATE_PROFILE_CHANNEL_ID = 1497905568416006285
ESPORT_CHANNEL_ID = 1498290487168077927
GUILD_ID = 1495617510341349448

PENDING_ROLE_ID = 1495620070380933123
YELLOW_CARD_ROLE_ID = 1495620138634838066 
REJECT_ROLE_ID = 1496364137037566075 
ALLOWED_ROLE_IDS = [1495619971873771641, 1496046389594034187, 1496046526806360226]

EMOJI_WAIT = f"<a:ro:1496010542563987558>"
EMOJI_PASS = f"<a:pass:1496008949407813762>"
EMOJI_NOPASS = f"<a:Nopass:1496364763502874824>"
EMOJI_TIKTUK = f"<a:tiktuk1:1496008772072509522>"
EMOJI_BOX = f"<a:boxbox:1496011068751876167>"
EMOJI_JANG = f"<a:jang1:1496009919244275732>"

tz = pytz.timezone('Asia/Bangkok')

# --- [3] ระบบเชื่อมต่อ Google Sheets ---
def connect_google_sheets():
    try:
        if not GOOGLE_CREDS:
            print("❌ Error: GOOGLE_CREDS_JSON ไม่ถูกตั้งค่าใน Environment")
            return None
        
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds_dict = json.loads(GOOGLE_CREDS)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        client = gspread.authorize(creds)
        return client.open("Police Bot").worksheet("ข้อมูลสมาชิกทั้งกิลด์")
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return None

# สร้างตัวแปร sheet ไว้ใช้งาน
sheet = connect_google_sheets()
last_reset_date = None 

def safe_update(range_name, values):
    global sheet
    for attempt in range(3):
        try:
            sheet.update(range_name, values)
            return True
        except:
            time.sleep(2)
            sheet = connect_google_sheets()
    return False

# --- [4] ฟังก์ชันดึงข้อมูลและ OCR ---
def get_clean_field(content, label):
    text = content.replace("**", "").replace("__", "").replace("`", "")
    lines = text.split('\n')
    for line in lines:
        if label in line:
            parts = re.split(r'[:：]', line)
            if len(parts) > 1:
                return ":".join(parts[1:]).strip()
    return "ไม่ระบุ"

async def verify_image_details(attachment, today_str):
    try:
        image_data = await attachment.read()
        img = Image.open(io.BytesIO(image_data))
        img = ImageEnhance.Contrast(img).enhance(1.8)
        # หมายเหตุ: Render อาจไม่มี Tesseract ลงไว้ให้ในตัว
        raw_text = pytesseract.image_to_string(img, lang='tha+eng', config='--psm 3').upper()
        raw_text += " " + pytesseract.image_to_string(img, lang='tha+eng', config='--psm 11').upper()
        
        keywords = ["VICTORY", "DEFEAT", "MVP", "SCORE", "SUMMARY", "สรุปคะแนน", "ชัยชนะ", "พ่ายแพ้"]
        is_game = any(kw in raw_text for kw in keywords)
        date_found = (today_str in raw_text) or (today_str.replace("-", "/") in raw_text)
        
        if not is_game: return False, "❌ รูปที่ส่งมาไม่ใช่รูปจบเกม ROV นะคะพี่ขา!"
        if not date_found: return False, f"❌ วันที่ในรูปไม่ตรงกับวันนี้ (ต้องการ {today_str}) ค่ะ!"
        return True, "ผ่าน"
    except Exception:
        return False, "❌ หนูอ่านรูปภาพไม่ได้ค่ะพี่ขา (OCR Error)"

# --- [5] สร้าง Class Bot และระบบ Auto-Reset ---
class PoliceMasterBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.all()
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        self.daily_reset_task.start()
        self.check_warning_task.start()
        await self.tree.sync()
        print(f"--- ✅ Police Bot V7.2 Online & Synced ---")

    @tasks.loop(seconds=30)
    async def daily_reset_task(self):
        global last_reset_date
        now = datetime.datetime.now(tz)
        today_obj = now.date()

        if last_reset_date is None:
            last_reset_date = today_obj
            return

        if today_obj > last_reset_date:
            try:
                all_data = sheet.get_all_values()
                if not all_data: return
                header = all_data[0]
                new_rows = [header]
                guild = self.get_guild(GUILD_ID)
                yellow_role = guild.get_role(YELLOW_CARD_ROLE_ID)

                for row in all_data[1:]:
                    while len(row) < 12: row.append("")
                    uid_str = row[0]
                    if not uid_str: continue
                    
                    uid = int(uid_str)
                    member = guild.get_member(uid)
                    
                    leave_until = row[9]
                    if leave_until:
                        try:
                            l_date = datetime.datetime.strptime(leave_until, "%Y-%m-%d").date()
                            if today_obj <= l_date:
                                row[6] = "ลา"
                                new_rows.append(row); continue
                        except: pass
                    
                    warns = int(row[4]) if row[4].isdigit() else 0
                    last_check_str = row[8]
                    if last_check_str and row[6] != "เช็คแล้ว":
                        try:
                            last_date = datetime.datetime.strptime(last_check_str, "%Y-%m-%d").date()
                            if (today_obj - last_date).days >= 3:
                                warns += 1
                                row[4] = str(warns)
                        except: pass
                    
                    if warns >= 3 and member:
                        try: await member.kick(reason="ใบเหลืองสะสมครบ 3 ใบค่ะ"); continue
                        except: pass
                    
                    if member and yellow_role:
                        if warns >= 1: await member.add_roles(yellow_role)
                        else: await member.remove_roles(yellow_role)

                    row[3], row[5], row[6], row[9] = "", "", "ยังไม่ได้เช็ค", ""
                    new_rows.append(row)
                
                sheet.clear()
                sheet.update('A1', new_rows)
                last_reset_date = today_obj
                print(f"✨ รีเซ็ตวันใหม่เรียบร้อยแล้วค่ะ")
            except Exception as e:
                print(f"❌ Reset Error: {e}")

    @tasks.loop(minutes=1)
    async def check_warning_task(self):
        now = datetime.datetime.now(tz)
        if now.hour == 22 and now.minute == 0:
            try:
                all_data = sheet.get_all_values()
                not_checked = [f"<@{r[0]}>" for r in all_data[1:] if r[6] not in ["เช็คแล้ว", "ลา"]]
                if not_checked:
                    ch = self.get_channel(NOTI_CHANNEL_ID)
                    if ch: await ch.send(f"{EMOJI_JANG} 22.00 น. แล้ว ใครยังไม่เช็คงานรีบส่งน้า!\n{' '.join(not_checked)}")
            except: pass

bot = PoliceMasterBot()

# --- [6] Events & Slash Commands ---

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name} ({bot.user.id})")

@bot.event
async def on_message(message):
    if message.author.bot: return
    
    if message.channel.id == ESPORT_CHANNEL_ID and "ยอมรับการลงทะเบียน" in message.content:
        ids = sheet.col_values(1)
        if str(message.author.id) in ids:
            idx = ids.index(str(message.author.id)) + 1
            safe_update(f'L{idx}', [["ลงทะเบียน E-sport แล้ว"]])
            await message.reply(f"✅ บันทึกข้อมูล E-sport ให้เรียบร้อยแล้วค่ะพี่!")

    if message.channel.id == REG_CHANNEL_ID and message.attachments:
        try:
            role = message.guild.get_role(PENDING_ROLE_ID)
            if role: await message.author.add_roles(role)
            await message.add_reaction(EMOJI_WAIT)
            embed = discord.Embed(
                title=f"{EMOJI_WAIT} หนูรับเรื่องการลงทะเบียนให้แล้วนะคะ",
                description="📍 **มารอที่ห้องรอเทส เวลา 20.00 น.** นะคะ\n⚠️ เรทได้ไม่เกิน 15 นาทีนะคะ",
                color=0x3498db
            )
            await message.reply(embed=embed, delete_after=120)
        except: pass

@bot.tree.command(name="checkin", description="เช็คชื่อเข้างาน (ดักคนเหลี่ยม)")
async def checkin(interaction: discord.Interaction, member1: discord.Member, image: discord.Attachment, member2: discord.Member=None, member3: discord.Member=None, member4: discord.Member=None):
    await interaction.response.defer(ephemeral=True)
    
    members_input = [member1, member2, member3, member4]
    team = [interaction.user] 
    for m in members_input:
        if m:
            if m.id == interaction.user.id:
                return await interaction.followup.send("❌ พี่จะแท็กชื่อตัวเองทำไมคะ ไม่เหลี่ยมสิคะ!")
            if m in team:
                return await interaction.followup.send(f"❌ พี่แท็ก {m.display_name} ซ้ำแล้วนะคะ")
            team.append(m)

    all_data = sheet.get_all_values()
    all_ids = [row[0] for row in all_data]
    
    for m in team:
        if str(m.id) not in all_ids: return await interaction.followup.send(f"❌ {m.mention} ไม่มีชื่อในระบบค่ะ")
    
    today = datetime.datetime.now(tz).strftime("%Y-%m-%d")
    is_ok, error_msg = await verify_image_details(image, today)
    if not is_ok: return await interaction.followup.send(error_msg)
    
    team_status = []
    for u in team:
        idx = all_ids.index(str(u.id)) + 1
        partners = ", ".join([m.display_name for m in team if m.id != u.id])
        warns_val = all_data[idx-1][4]
        warns_int = int(warns_val) if warns_val.isdigit() else 0
        
        safe_update(f'D{idx}', [[partners]])
        safe_update(f'F{idx}', [[image.url]])
        safe_update(f'G{idx}', [["เช็คแล้ว"]])
        safe_update(f'I{idx}', [[today]])
        team_status.append(f"{u.mention} (🟡 {warns_int} ใบ)")
    
    embed = discord.Embed(title=f"{EMOJI_TIKTUK} เช็คชื่อสำเร็จแล้วค่ะพี่", color=0x2ecc71)
    embed.add_field(name=f"{EMOJI_BOX} สมาชิกทีม", value=" | ".join(team_status), inline=False)
    embed.set_image(url=image.url)
    await interaction.channel.send(content=f"✅ {interaction.user.mention} ส่งงานเรียบร้อยค่ะพี่ขา", embed=embed)
    await interaction.followup.send("เช็คชื่อสำเร็จแล้วน้า!")

# --- [7] รันบอท (จุดสำคัญ) ---
if __name__ == "__main__":
    if TOKEN:
        keep_alive()  # เริ่มระบบ Flask เพื่อค้างสถานะบน Render
        bot.run(TOKEN)
    else:
        print("❌ Error: ไม่พบ DISCORD_TOKEN ใน Environment Variables!")
