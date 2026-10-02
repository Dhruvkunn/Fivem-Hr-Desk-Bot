import os, sqlite3, logging
from datetime import datetime, timezone
import discord
from discord.ext import commands, tasks
from discord import app_commands

DB='dreamcity_hr.db'
TOKEN=os.getenv('DISCORD_TOKEN','')
GUILD_ID=int(os.getenv('GUILD_ID','0') or 0)
HR_ROLE=os.getenv('HR_ROLE','HR')
COMMAND_ROLE=os.getenv('COMMAND_ROLE','Department Command')
HR_CHANNEL=os.getenv('HR_CHANNEL','hr-management')
LOG_CHANNEL=os.getenv('LOG_CHANNEL','hr-audit-log')
APP_CHANNEL=os.getenv('APP_CHANNEL','government-applications')
INTERVIEW_CHANNEL=os.getenv('INTERVIEW_CHANNEL','hr-interviews')

logging.basicConfig(level=logging.INFO)


def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

def db():
    c=sqlite3.connect(DB)
    c.row_factory=sqlite3.Row
    return c

def init_db():
    c=db(); cur=c.cursor()
    cur.executescript('''
    CREATE TABLE IF NOT EXISTS departments(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT UNIQUE NOT NULL,role_id INTEGER DEFAULT 0,active INTEGER DEFAULT 1);
    CREATE TABLE IF NOT EXISTS employees(id INTEGER PRIMARY KEY AUTOINCREMENT,discord_id INTEGER UNIQUE NOT NULL,employee_no TEXT UNIQUE NOT NULL,name TEXT NOT NULL,department TEXT,rank TEXT,status TEXT DEFAULT 'Active',joined_at TEXT NOT NULL,supervisor TEXT DEFAULT '',hours REAL DEFAULT 0,leave_days INTEGER DEFAULT 14);
    CREATE TABLE IF NOT EXISTS applications(id INTEGER PRIMARY KEY AUTOINCREMENT,discord_id INTEGER NOT NULL,name TEXT NOT NULL,department TEXT NOT NULL,answers TEXT NOT NULL,status TEXT DEFAULT 'Pending',submitted_at TEXT NOT NULL,reviewer_id INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS interviews(id INTEGER PRIMARY KEY AUTOINCREMENT,application_id INTEGER NOT NULL,applicant_id INTEGER NOT NULL,interviewer_id INTEGER NOT NULL,scheduled_at TEXT NOT NULL,status TEXT DEFAULT 'Scheduled',notes TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS actions(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,type TEXT NOT NULL,details TEXT NOT NULL,actor_id INTEGER NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS attendance(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,clock_in TEXT NOT NULL,clock_out TEXT DEFAULT '',minutes INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS certifications(id INTEGER PRIMARY KEY AUTOINCREMENT,employee_id INTEGER NOT NULL,name TEXT NOT NULL,expires_at TEXT DEFAULT '',status TEXT DEFAULT 'Active');
    CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_id INTEGER NOT NULL,action TEXT NOT NULL,target TEXT NOT NULL,details TEXT DEFAULT '',created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
    ''')
    c.commit(); c.close()

def audit(actor, action, target, details=''):
    c=db(); c.execute('INSERT INTO audit(actor_id,action,target,details,created_at) VALUES(?,?,?,?,?)',(actor,action,target,details,now())); c.commit(); c.close()

def is_manager(member):
    return member.guild_permissions.manage_guild or any(r.name in {HR_ROLE,COMMAND_ROLE} for r in member.roles)

def get_emp(uid):
    c=db(); r=c.execute('SELECT * FROM employees WHERE discord_id=?',(uid,)).fetchone(); c.close(); return r

def next_emp_no():
    c=db(); n=c.execute('SELECT COUNT(*) n FROM employees').fetchone()['n']+1; c.close(); return f'GOV-{n:05d}'

class HRBot(commands.Bot):
    def __init__(self):
        intents=discord.Intents.default(); intents.members=True; intents.guilds=True
        super().__init__(command_prefix='!', intents=intents)
    async def setup_hook(self):
        init_db()
        if GUILD_ID:
            guild=discord.Object(id=GUILD_ID); self.tree.copy_global_to(guild=guild); await self.tree.sync(guild=guild)
        else: await self.tree.sync()
        expiry_check.start()

bot=HRBot()

async def get_or_create_channel(guild,name,category=None):
    ch=discord.utils.get(guild.text_channels,name=name)
    if ch: return ch
    return await guild.create_text_channel(name,category=category,reason='Dream City HR setup')

async def get_or_create_role(guild,name):
    r=discord.utils.get(guild.roles,name=name)
    return r or await guild.create_role(name=name,reason='Dream City HR setup')

class ApplicationModal(discord.ui.Modal,title='Dream City Government Application'):
    department=discord.ui.TextInput(label='Department',placeholder='Police / EMS / Fire / DOJ / Government',max_length=50)
    experience=discord.ui.TextInput(label='RP / Department Experience',style=discord.TextStyle.paragraph,max_length=1000)
    why=discord.ui.TextInput(label='Why do you want to join?',style=discord.TextStyle.paragraph,max_length=1000)
    availability=discord.ui.TextInput(label='Availability',placeholder='Example: 6 PM - 11 PM IST',max_length=200)
    about=discord.ui.TextInput(label='Tell us about yourself',style=discord.TextStyle.paragraph,max_length=1000)
    async def on_submit(self,interaction):
        c=db(); cur=c.execute('INSERT INTO applications(discord_id,name,department,answers,submitted_at) VALUES(?,?,?,?,?)',(interaction.user.id,str(interaction.user),self.department.value,f'Experience: {self.experience.value}\nWhy: {self.why.value}\nAvailability: {self.availability.value}\nAbout: {self.about.value}',now())); app_id=cur.lastrowid; c.commit(); c.close()
        audit(interaction.user.id,'APPLICATION_SUBMITTED',str(app_id),self.department.value)
        await interaction.response.send_message(f'✅ Application **#{app_id}** submitted for **{self.department.value}**. HR will review it.',ephemeral=True)
        ch=discord.utils.get(interaction.guild.text_channels,name=APP_CHANNEL)
        if ch:
            e=discord.Embed(title=f'📋 New Government Application #{app_id}',description=f'**Applicant:** {interaction.user.mention}\n**Department:** {self.department.value}\n**Status:** 🟡 Pending',color=0x2b6cb0)
            e.add_field(name='Experience',value=self.experience.value[:1024],inline=False); e.add_field(name='Why',value=self.why.value[:1024],inline=False)
            await ch.send(embed=e,view=ApplicationReviewView(app_id))

class ApplicationReviewView(discord.ui.View):
    def __init__(self,app_id): super().__init__(timeout=None); self.app_id=app_id
    @discord.ui.button(label='Interview',style=discord.ButtonStyle.primary,emoji='🎤')
    async def interview(self,interaction,button):
        if not is_manager(interaction.user): return await interaction.response.send_message('❌ HR/Command only.',ephemeral=True)
        c=db(); a=c.execute('SELECT * FROM applications WHERE id=?',(self.app_id,)).fetchone(); c.close()
        if not a: return await interaction.response.send_message('Application not found.',ephemeral=True)
        await interaction.response.send_modal(InterviewModal(self.app_id,a['discord_id']))
    @discord.ui.button(label='Accept',style=discord.ButtonStyle.success,emoji='✅')
    async def accept(self,interaction,button): await finish_application(interaction,self.app_id,True)
    @discord.ui.button(label='Deny',style=discord.ButtonStyle.danger,emoji='❌')
    async def deny(self,interaction,button): await finish_application(interaction,self.app_id,False)

async def finish_application(interaction,app_id,accepted):
    if not is_manager(interaction.user): return await interaction.response.send_message('❌ HR/Command only.',ephemeral=True)
    c=db(); a=c.execute('SELECT * FROM applications WHERE id=?',(app_id,)).fetchone()
    if not a: c.close(); return await interaction.response.send_message('Application not found.',ephemeral=True)
    status='Accepted' if accepted else 'Denied'; c.execute('UPDATE applications SET status=?,reviewer_id=? WHERE id=?',(status,interaction.user.id,app_id))
    emp=None
    if accepted:
        no=next_emp_no(); c.execute('INSERT OR IGNORE INTO employees(discord_id,employee_no,name,department,rank,joined_at) VALUES(?,?,?,?,?,?)',(a['discord_id'],no,a['name'],a['department'],'Cadet',now()))
        emp=c.execute('SELECT * FROM employees WHERE discord_id=?',(a['discord_id'],)).fetchone()
    c.commit(); c.close(); audit(interaction.user.id,'APPLICATION_'+status.upper(),str(app_id),a['department'])
    await interaction.response.send_message(f'✅ Application #{app_id} **{status}**.',ephemeral=True)
    member=interaction.guild.get_member(a['discord_id'])
    if member and accepted:
        await sync_roles(member,a['department'],'Cadet')
        try: await member.send(f'🎉 Your Dream City Government application has been **accepted**!\nDepartment: {a["department"]}\nRank: Cadet\nEmployee ID: {emp["employee_no"]}')
        except discord.Forbidden: pass

class InterviewModal(discord.ui.Modal,title='Schedule HR Interview'):
    date_time=discord.ui.TextInput(label='Date & time (ISO)',placeholder='2026-10-05 20:00',max_length=30)
    interviewer=discord.ui.TextInput(label='Interviewer Discord ID',placeholder='123456789012345678',max_length=30)
    notes=discord.ui.TextInput(label='Notes',style=discord.TextStyle.paragraph,required=False,max_length=1000)
    def __init__(self,app_id,applicant_id): super().__init__(); self.app_id=app_id; self.applicant_id=applicant_id
    async def on_submit(self,interaction):
        c=db(); c.execute('INSERT INTO interviews(application_id,applicant_id,interviewer_id,scheduled_at,notes) VALUES(?,?,?,?,?)',(self.app_id,self.applicant_id,int(self.interviewer.value),self.date_time.value,self.notes.value)); c.execute('UPDATE applications SET status=? WHERE id=?',('Interview Scheduled',self.app_id)); c.commit(); c.close(); audit(interaction.user.id,'INTERVIEW_SCHEDULED',str(self.app_id),self.date_time.value)
        await interaction.response.send_message(f'🎤 Interview for application #{self.app_id} scheduled for **{self.date_time.value}**.',ephemeral=True)
        ch=discord.utils.get(interaction.guild.text_channels,name=INTERVIEW_CHANNEL)
        if ch: await ch.send(f'🎤 **Interview Scheduled**\nApplicant: <@{self.applicant_id}>\nInterviewer: <@{self.interviewer.value}>\nTime: **{self.date_time.value}**\nApplication: #{self.app_id}')

class MainHRView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    @discord.ui.button(label='Apply',style=discord.ButtonStyle.success,emoji='📝')
    async def apply(self,i,b): await i.response.send_modal(ApplicationModal())
    @discord.ui.button(label='My HR Profile',style=discord.ButtonStyle.primary,emoji='👤')
    async def profile(self,i,b):
        e=get_emp(i.user.id)
        if not e: return await i.response.send_message('No HR profile found.',ephemeral=True)
        await i.response.send_message(embed=employee_embed(e),ephemeral=True)

async def sync_roles(member,department,rank):
    # Safe role sync: only roles matching configured department/rank are touched.
    names={department,rank}
    for r in list(member.roles):
        if r.name in names: continue
        # Don't remove arbitrary roles; administrators can configure role sync later.
    for name in names:
        r=discord.utils.get(member.guild.roles,name=name)
        if r:
            try: await member.add_roles(r,reason='Dream City HR sync')
            except discord.Forbidden: pass

def employee_embed(e):
    em=discord.Embed(title='🏛️ Dream City HR Profile',color=0x5865f2)
    em.add_field(name='Employee',value=f"{e['name']}\n`{e['employee_no']}`",inline=True)
    em.add_field(name='Department / Rank',value=f"{e['department']}\n**{e['rank']}**",inline=True)
    em.add_field(name='Status',value=e['status'],inline=True)
    em.add_field(name='Joined',value=e['joined_at'][:10],inline=True)
    em.add_field(name='Duty Hours',value=f"{e['hours']:.2f}",inline=True)
    em.add_field(name='Leave Remaining',value=str(e['leave_days']),inline=True)
    return em

@bot.tree.command(name='hr',description='Open the Dream City HR control panel')
async def hr(interaction:discord.Interaction):
    e=discord.Embed(title='🏛️ DREAM CITY — HUMAN RESOURCES',description='Central personnel management system.\n\n📝 Recruitment\n🎤 Interviews\n👤 Employee records\n📈 Promotions\n⚠️ Discipline\n🏖️ Leave\n🎖️ Training & certifications\n📊 Reports\n🔄 Transfers\n🧾 Audit logs',color=0x5865f2)
    await interaction.response.send_message(embed=e,view=MainHRView(),ephemeral=not is_manager(interaction.user))

@bot.tree.command(name='setup_hr',description='Create the basic HR channels and roles')
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_hr(interaction):
    guild=interaction.guild; hr_role=await get_or_create_role(guild,HR_ROLE); cmd_role=await get_or_create_role(guild,COMMAND_ROLE)
    category=discord.utils.get(guild.categories,name='🏛️ DREAM CITY HR') or await guild.create_category('🏛️ DREAM CITY HR')
    for n in [HR_CHANNEL,LOG_CHANNEL,APP_CHANNEL,INTERVIEW_CHANNEL]: await get_or_create_channel(guild,n,category)
    await interaction.response.send_message(f'✅ HR foundation created. Roles: {hr_role.mention}, {cmd_role.mention}.',ephemeral=True)

@bot.tree.command(name='employee',description='View an employee HR profile')
@app_commands.describe(member='Employee to view')
async def employee(interaction,member:discord.Member):
    e=get_emp(member.id)
    if not e: return await interaction.response.send_message('No employee record.',ephemeral=True)
    await interaction.response.send_message(embed=employee_embed(e),ephemeral=True)

@bot.tree.command(name='promote',description='Promote an employee')
@app_commands.describe(member='Employee',new_rank='New rank',reason='Reason')
async def promote(interaction,member:discord.Member,new_rank:str,reason:str):
    if not is_manager(interaction.user): return await interaction.response.send_message('❌ HR/Command only.',ephemeral=True)
    e=get_emp(member.id)
    if not e: return await interaction.response.send_message('No employee record.',ephemeral=True)
    c=db(); c.execute('UPDATE employees SET rank=? WHERE id=?',(new_rank,e['id'])); c.execute('INSERT INTO actions(employee_id,type,details,actor_id,created_at) VALUES(?,?,?,?,?)',(e['id'],'PROMOTION',f'{e["rank"]} → {new_rank}: {reason}',interaction.user.id,now())); c.commit(); c.close(); audit(interaction.user.id,'PROMOTION',e['employee_no'],reason)
    await sync_roles(member,e['department'],new_rank); await interaction.response.send_message(f'📈 **{member.display_name}** promoted to **{new_rank}**.')

@bot.tree.command(name='discipline',description='Record disciplinary action')
async def discipline(interaction,member:discord.Member,action_type:str,details:str):
    if not is_manager(interaction.user): return await interaction.response.send_message('❌ HR/Command only.',ephemeral=True)
    e=get_emp(member.id)
    if not e: return await interaction.response.send_message('No employee record.',ephemeral=True)
    c=db(); c.execute('INSERT INTO actions(employee_id,type,details,actor_id,created_at) VALUES(?,?,?,?,?)',(e['id'],'DISCIPLINE',f'{action_type}: {details}',interaction.user.id,now())); c.commit(); c.close(); audit(interaction.user.id,'DISCIPLINE',e['employee_no'],f'{action_type}: {details}')
    await interaction.response.send_message(f'⚠️ Discipline recorded for **{member.display_name}**: **{action_type}**.',ephemeral=True)

@bot.tree.command(name='loa',description='Request or review leave')
async def loa(interaction,days:int,reason:str):
    e=get_emp(interaction.user.id)
    if not e: return await interaction.response.send_message('You do not have an HR employee profile.',ephemeral=True)
    if days<1 or days>e['leave_days']: return await interaction.response.send_message(f'You have {e["leave_days"]} leave days remaining.',ephemeral=True)
    c=db(); c.execute('UPDATE employees SET leave_days=leave_days-? WHERE id=?',(days,e['id'])); c.execute('INSERT INTO actions(employee_id,type,details,actor_id,created_at) VALUES(?,?,?,?,?)',(e['id'],'LOA',f'{days} days: {reason}',interaction.user.id,now())); c.commit(); c.close(); audit(interaction.user.id,'LOA_REQUEST',e['employee_no'],f'{days} days')
    await interaction.response.send_message(f'🏖️ LOA recorded for **{days} day(s)**. Remaining: **{e["leave_days"]-days}**.',ephemeral=True)

@bot.tree.command(name='certification',description='Add an employee certification')
async def certification(interaction,member:discord.Member,name:str,expires:str=''):
    if not is_manager(interaction.user): return await interaction.response.send_message('❌ HR/Command only.',ephemeral=True)
    e=get_emp(member.id)
    if not e: return await interaction.response.send_message('No employee record.',ephemeral=True)
    c=db(); c.execute('INSERT INTO certifications(employee_id,name,expires_at) VALUES(?,?,?)',(e['id'],name,expires)); c.commit(); c.close(); audit(interaction.user.id,'CERTIFICATION_ADDED',e['employee_no'],name)
    await interaction.response.send_message(f'🎖️ Added **{name}** certification to {member.mention}.',ephemeral=True)

@bot.tree.command(name='award',description='Give an employee an award')
async def award(interaction,member:discord.Member,award_name:str,reason:str):
    if not is_manager(interaction.user): return await interaction.response.send_message('❌ HR/Command only.',ephemeral=True)
    e=get_emp(member.id)
    if not e: return await interaction.response.send_message('No employee record.',ephemeral=True)
    c=db(); c.execute('INSERT INTO actions(employee_id,type,details,actor_id,created_at) VALUES(?,?,?,?,?)',(e['id'],'AWARD',f'{award_name}: {reason}',interaction.user.id,now())); c.commit(); c.close(); audit(interaction.user.id,'AWARD',e['employee_no'],award_name)
    await interaction.response.send_message(f'🏅 **{award_name}** awarded to {member.mention}.')

@bot.tree.command(name='transfer',description='Transfer an employee to another department')
async def transfer(interaction,member:discord.Member,department:str,reason:str):
    if not is_manager(interaction.user): return await interaction.response.send_message('❌ HR/Command only.',ephemeral=True)
    e=get_emp(member.id)
    if not e: return await interaction.response.send_message('No employee record.',ephemeral=True)
    old=e['department']; c=db(); c.execute('UPDATE employees SET department=? WHERE id=?',(department,e['id'])); c.execute('INSERT INTO actions(employee_id,type,details,actor_id,created_at) VALUES(?,?,?,?,?)',(e['id'],'TRANSFER',f'{old} → {department}: {reason}',interaction.user.id,now())); c.commit(); c.close(); audit(interaction.user.id,'TRANSFER',e['employee_no'],f'{old} → {department}')
    await sync_roles(member,department,e['rank']); await interaction.response.send_message(f'🔄 {member.mention} transferred **{old} → {department}**.')

@bot.tree.command(name='clockin',description='Clock into duty')
async def clockin(interaction):
    e=get_emp(interaction.user.id)
    if not e: return await interaction.response.send_message('No employee profile.',ephemeral=True)
    c=db(); open_shift=c.execute('SELECT * FROM attendance WHERE employee_id=? AND clock_out=""',(e['id'],)).fetchone()
    if open_shift: c.close(); return await interaction.response.send_message('You are already clocked in.',ephemeral=True)
    c.execute('INSERT INTO attendance(employee_id,clock_in) VALUES(?,?)',(e['id'],now())); c.commit(); c.close(); await interaction.response.send_message('🟢 **Clocked in.** Your shift has started.',ephemeral=True)

@bot.tree.command(name='clockout',description='Clock out of duty')
async def clockout(interaction):
    e=get_emp(interaction.user.id)
    if not e: return await interaction.response.send_message('No employee profile.',ephemeral=True)
    c=db(); r=c.execute('SELECT * FROM attendance WHERE employee_id=? AND clock_out="" ORDER BY id DESC LIMIT 1',(e['id'],)).fetchone()
    if not r: c.close(); return await interaction.response.send_message('You are not clocked in.',ephemeral=True)
    start=datetime.fromisoformat(r['clock_in']); mins=max(0,int((datetime.now(timezone.utc)-start).total_seconds()/60)); c.execute('UPDATE attendance SET clock_out=?,minutes=? WHERE id=?',(now(),mins,r['id'])); c.execute('UPDATE employees SET hours=hours+? WHERE id=?',(mins/60,e['id'])); c.commit(); c.close(); await interaction.response.send_message(f'🔴 **Clocked out.** Shift: **{mins//60}h {mins%60}m**.',ephemeral=True)

@bot.tree.command(name='report',description='View government HR statistics')
async def report(interaction):
    if not is_manager(interaction.user): return await interaction.response.send_message('❌ HR/Command only.',ephemeral=True)
    c=db(); total=c.execute('SELECT COUNT(*) n FROM employees').fetchone()['n']; active=c.execute('SELECT COUNT(*) n FROM employees WHERE status="Active"').fetchone()['n']; apps=c.execute('SELECT COUNT(*) n FROM applications WHERE status LIKE "Pending%"').fetchone()['n']; depts=c.execute('SELECT department,COUNT(*) n FROM employees GROUP BY department ORDER BY n DESC').fetchall(); c.close()
    text='\n'.join(f'**{r["department"] or "Unassigned"}:** {r["n"]}' for r in depts) or 'No employees yet.'
    em=discord.Embed(title='📊 Dream City Government HR Report',color=0x5865f2); em.add_field(name='Employees',value=f'{total} total\n{active} active',inline=True); em.add_field(name='Pending Applications',value=str(apps),inline=True); em.add_field(name='Departments',value=text,inline=False); await interaction.response.send_message(embed=em,ephemeral=True)

@bot.tree.command(name='applications',description='List recent applications')
async def applications(interaction):
    if not is_manager(interaction.user): return await interaction.response.send_message('❌ HR/Command only.',ephemeral=True)
    c=db(); rows=c.execute('SELECT * FROM applications ORDER BY id DESC LIMIT 10').fetchall(); c.close()
    if not rows: return await interaction.response.send_message('No applications yet.',ephemeral=True)
    text='\n'.join(f'**#{r["id"]}** — <@{r["discord_id"]}> — {r["department"]} — **{r["status"]}**' for r in rows)
    await interaction.response.send_message(embed=discord.Embed(title='📋 Recent Applications',description=text,color=0x5865f2),ephemeral=True)

@bot.tree.command(name='audit',description='View recent HR audit logs')
async def audit_cmd(interaction):
    if not interaction.user.guild_permissions.manage_guild: return await interaction.response.send_message('❌ Server management permission required.',ephemeral=True)
    c=db(); rows=c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 15').fetchall(); c.close(); text='\n'.join(f'`{r["created_at"][:19]}` <@{r["actor_id"]}> **{r["action"]}** `{r["target"]}` {r["details"][:80]}' for r in rows) or 'No audit records.'
    await interaction.response.send_message(embed=discord.Embed(title='🧾 HR Audit Log',description=text,color=0x5865f2),ephemeral=True)

@tasks.loop(hours=24)
async def expiry_check():
    # Daily reminder for certifications expiring soon.
    c=db(); rows=c.execute('SELECT c.*,e.discord_id,e.name FROM certifications c JOIN employees e ON e.id=c.employee_id WHERE expires_at != ""').fetchall(); c.close()
    for r in rows:
        try:
            d=datetime.fromisoformat(r['expires_at']); days=(d-datetime.now(timezone.utc)).days
            if 0<=days<=3:
                for g in bot.guilds:
                    ch=discord.utils.get(g.text_channels,name=LOG_CHANNEL)
                    if ch: await ch.send(f'⚠️ **Certification Expiring** — {r["name"]} (<@{r["discord_id"]}>) — **{r["name"]}** expires in {days} day(s).')
        except Exception: pass

@bot.event
async def on_ready():
    logging.info('Dream City HR online as %s',bot.user)

if not TOKEN:
    print('Set DISCORD_TOKEN before running the bot.')
else:
    bot.run(TOKEN)
