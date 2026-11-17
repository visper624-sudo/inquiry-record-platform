# -*- coding: utf-8 -*-
"""演示数据种子脚本。

生成内容全部为**虚构脱敏数据**：人名、身份证号、手机号、银行卡号、网址、APP 名称均为占位构造，
不涉及任何真实案件与真实个人信息。重新生成：
    python backend/seed.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import config  # noqa: E402
from config import DEMO_DIR, MATERIAL_DIR  # noqa: E402
from core import audit, db, security  # noqa: E402
from services import templates_svc  # noqa: E402

DEMO_PASSWORD = "123456"

ORGS = [
    ("O001", "江城市公安局", None, 1, "JCS", 1),
    ("O002", "江城市公安局城东分局", "O001", 2, "JCS-CD", 10),
    ("O003", "城东分局长虹路派出所", "O002", 3, "JCS-CD-CHL", 11),
    ("O004", "城东分局望湖路派出所", "O002", 3, "JCS-CD-WHL", 12),
    ("O005", "江城市公安局反诈中心", "O001", 2, "JCS-FZZX", 20),
]

USERS = [
    ("U001", "100001", "陈立国", "admin", "O001", "系统管理员"),
    ("U002", "100101", "张伟", "police", "O003", "办案民警"),
    ("U003", "100102", "李静", "police", "O003", "办案民警"),
    ("U004", "100201", "王海涛", "analyst", "O005", "反诈研判员"),
    ("U005", "100103", "赵磊", "police", "O004", "办案民警"),
]

EXTERNAL_CASES = [
    ("EXT001", "zhiyanpan", "ZYP2026-1016-0031", "虚假网络投资理财",
     "受害人李芳在股票交流群被陌生网友添加微信，对方自称国盛证券客服经理并推荐“国盛智投”APP炒股，"
     "受害人扫码下载后通过手机银行分三笔转账共80万元，平台显示盈利但无法提现，发现被骗。",
     "李芳", "13800000000", "城东分局长虹路派出所", "2026-10-25 09:12:00", "城东区长虹路88号", "张伟"),
    ("EXT002", "zhianguan", "ZAG2026-1102-0118", "刷单返利",
     "受害人王强在短视频平台看到兼职刷单广告，添加客服后被拉入任务群，按指示垫付下单获取小额返利，"
     "后续被要求连续完成大额任务，累计转账6.8万元后无法提现。",
     "王强", "13900000000", "城东分局长虹路派出所", "2026-11-02 15:40:00", "城东区望湖路21号", "张伟"),
    ("EXT003", "zhiyanpan", "ZYP2026-1105-0107", "冒充公检法",
     "受害人接到自称江城市公安局刑侦支队电话，称其身份信息被用于洗钱，要求将资金转入“安全账户”接受审查，"
     "受害人按指示下载视频会议软件并共享屏幕，转账12万元。",
     "孙丽", "13600000000", "城东分局望湖路派出所", "2026-11-05 10:20:00", "城东区望湖路5号", "赵磊"),
    ("EXT004", "zhianguan", "ZAG2026-1108-0062", "冒充电商物流客服",
     "受害人接到自称快递公司客服电话，称其包裹丢失可三倍赔付，诱导受害人开启屏幕共享并从借贷平台借款后转账。",
     "周敏", "13700000000", "城东分局长虹路派出所", "2026-11-08 16:05:00", "城东区长虹路12号", "李静"),
    ("EXT005", "zhiyanpan", "ZYP2026-1110-0024", "网络婚恋交友",
     "受害人在婚恋交友网站认识自称海外高管的男子，对方以内部投资渠道名义引导受害人在虚假平台充值23万元后无法提现。",
     "吴婷", "13500000000", "城东分局长虹路派出所", "2026-11-10 11:30:00", "城东区望湖路33号", "张伟"),
    ("EXT006", "zhianguan", "ZAG2026-1112-0090", "虚假征信",
     "受害人接到自称银保监会工作人员电话，称其校园贷账户未注销将影响征信，按指示在多个平台贷款后转账“验资”，损失4.2万元。",
     "郑浩", "13400000000", "城东分局望湖路派出所", "2026-11-12 09:45:00", "城东区望湖路66号", "赵磊"),
]

# 案情答复库：按五流字段 key 注入，保证抽取结果可被验证
ANSWERS_INVEST = {
    "victim_identity": "我叫李芳，女，1990年3月出生，身份证号110101199001010000，户籍地江城市城东区长虹路88号，职业是公司财务，联系电话13800000000。",
    "victim_contact": "我的手机号是13800000000，一直正常使用。",
    "suspect_claim_identity": "对方自称是国盛证券的客服经理，还说自己是“金牌导师”，承诺带我炒股稳赚不赔。",
    "acquaintance": "我是在一个股票交流群里被对方主动添加微信认识的。",
    "agent_person": "群里还有一个自称“助理”的人，负责发二维码链接和催我充值。",
    "gang_role": "我不清楚具体分工，感觉有人讲课喊单，有人催缴资金，还有人专门对接收款账户。",
    "call_number": "对方用17000000000这个号码给我打过电话，后来也换过网络电话。",
    "call_time": "第一次通话是10月16日上午10时左右，之后几乎每天都联系。",
    "call_duration": "每次通话大约二三十分钟。",
    "caller_role": "电话里对方自称是客服经理和导师助理。",
    "sms_content": "对方给我发过一条短信，内容是一个下载链接和验证码。",
    "qr_code": "我是扫了群里发的二维码下载APP的，当时还有一个网页链接。",
    "url_domain": "平台网址是 www.gszc-invest.com ，现在打开已经显示错误。",
    "app_name": "对方让我下载的APP名称叫“国盛智投”。",
    "chat_account": "对方微信账号是 gszc888，另外还有一个QQ号 800012345。",
    "screen_share": "对方让我开过屏幕共享，说是帮我操作入金。",
    "login_ip": "我不懂IP地址，手机型号是华为Mate60 Pro。",
    "transfer_time": "第一笔是10月18日14时06分，后面几天又转了两次。",
    "transfer_amount": "第一笔30万元，第二笔30万元，第三笔20万元。",
    "pay_channel": "都是通过手机银行转账的。",
    "payer_card": "我用的是本人名下的工商银行卡，卡号6222020000000001234。",
    "counterparty_bank": "对方收款账户显示是建设银行。",
    "counterparty_card": "",          # 故意留空：制造资金流重点缺口
    "counterparty_name": "",          # 故意留空
    "serial_no": "",
    "total_loss": "累计损失80万元，另外还有一笔3万元的“解冻保证金”没有转成功。",
}

ANSWERS_BRUSHING = {
    "victim_identity": "我叫王强，男，1992年5月出生，身份证号110101199205150000，户籍地江城市城东区望湖路21号，职业是外卖骑手，联系电话13900000000。",
    "victim_contact": "我的手机号是13900000000。",
    "suspect_claim_identity": "对方自称是“任务宝”平台的客服，说是做兼职刷单任务。",
    "acquaintance": "我是在短视频平台看到兼职广告，私信对方后加的微信。",
    "agent_person": "有一个自称“派单员”的人负责在群里发任务。",
    "gang_role": "群里有人发任务、有人负责结算佣金，还有一个人专门让我转账。",
    "call_number": "对方用17100000000给我打过电话，让我到平台上操作。",
    "call_time": "11月2日下午13时左右开始联系的。",
    "call_duration": "第一次通话大约十分钟。",
    "caller_role": "来电自称是平台客服。",
    "sms_content": "对方发过一条短信，里面是任务链接。",
    "qr_code": "我扫了微信群里发的二维码下载的APP。",
    "url_domain": "平台网址是 www.shuadan-task.top ，现在还能打开。",
    "app_name": "APP名称叫“任务宝”。",
    "chat_account": "对方微信账号是 sdtask01，昵称是“派单员小陈”。",
    "screen_share": "没有让我开屏幕共享。",
    "login_ip": "我用的是小米14手机。",
    "transfer_time": "11月2日13时20分转了第一笔，后面又转了4笔。",
    "transfer_amount": "第一笔5000元，后面每笔1万到2万元不等。",
    "pay_channel": "通过支付宝转账的。",
    "payer_card": "我的钱是从农业银行卡转出去的，卡号6228480000000001234。",
    "counterparty_bank": "对方账户显示是农业银行。",
    "counterparty_card": "对方卡号是6217000000000005678。",
    "counterparty_name": "开户名是“陈某”。",
    "serial_no": "最后一张转账凭证的流水号是2026110200123456。",
    "total_loss": "累计损失6.8万元，佣金只返了几百块。",
}

# 非要素类问题的答复池（按章节轮换，避免十条一模一样的“听清楚了”）
DEFAULT_ANSWERS = {
    "opening": [
        "听清楚了，我会如实回答。",
        "明白，我一定配合。",
        "知道了，我如实说。",
        "听清楚了，没有异议。",
        "清楚，我按实际情况回答。",
    ],
    "contact": [
        "我是通过网上认识的，具体渠道记不太清了。",
        "对方先加的我好友，我当时没多想。",
        "是在一个群里看到信息后联系上的。",
        "对方主动发消息，说可以带我一起做。",
    ],
    "process": [
        "我记得大概的过程，可以把知道的都告诉你们。",
        "当时没想那么多，对方说什么我就照做了。",
        "具体步骤我记不太清，手机上应该还有记录。",
        "我按对方发来的链接一步步操作的。",
    ],
    "loss": [
        "转账的金额和凭证我都整理好了。",
        "钱是分几笔转出去的，我记得大概数。",
        "我手机银行里能查到每一笔记录。",
        "我没想到是骗局，转完就联系不上了。",
    ],
    "evidence": [
        "聊天记录、转账截图我都保存着，可以提供给公安机关。",
        "我手机里还有对方的微信和几次通话记录。",
        "转账凭证和银行流水我可以去柜台打印。",
        "需要的话我可以把手机交给你们取证。",
    ],
}

RECORD_SPECS = [
    {
        "id": "RC202611160001",
        "case_no": "JC20261116001",
        "category": "虚假网络投资理财",
        "brief": "2026年10月16日，受害人李芳被陌生网友拉入“股票投资群”，对方自称国盛证券客服经理，以导师荐股、"
                 "内幕消息、稳赚不赔为诱饵，推荐“国盛智投”APP。受害人扫码下载后在虚假平台充值，先后通过手机银行"
                 "三次转账共80万元，平台显示盈利但无法提现，对方又以缴纳解冻费、保证金为由继续索要资金，受害人发现被骗报案。",
        "reporter": ("李芳", "13800000000", "110101199001010000"),
        "location": "城东区长虹路88号",
        "org": "O003",
        "police": "U002",
        "templates": ["T03", "T00"],
        "answers": ANSWERS_INVEST,
        "mode": "partial",
        "excluded": ["delivery"],
        "stage": "inquiry",
        "elapsed": 1820,
        "created_days": 6,
        "start_hour": 9,
        "start_minute": 12,
        "materials": [
            ("转账凭证_30万元.png", "手机银行转账成功截图，收款方账户信息部分打码"),
            ("聊天记录_导师荐股.png", "微信聊天记录截图，对方以稳赚不赔诱导充值"),
        ],
    },
    {
        "id": "RC202611160002",
        "case_no": "JC20261116002",
        "category": "刷单返利",
        "brief": "受害人王强在短视频平台看到兼职刷单广告，添加客服后被拉入任务群，按指示垫付下单获取小额返利，"
                 "后续被要求连续完成大额任务，累计转账6.8万元后无法提现，对方要求缴纳解冻费，受害人发现被骗。",
        "reporter": ("王强", "13900000000", "110101199205150000"),
        "location": "城东区望湖路21号",
        "org": "O003",
        "police": "U002",
        "templates": ["T05", "T00"],
        "answers": ANSWERS_BRUSHING,
        "mode": "full",
        "excluded": ["delivery"],
        "stage": "completed",
        "elapsed": 2680,
        "created_days": 9,
        "start_hour": 9,
        "start_minute": 20,
        "materials": [("转账流水_6.8万元.png", "支付宝转账流水截图共计6.8万元")],
    },
    {
        "id": "RC202611160003",
        "case_no": "JC20261116003",
        "category": "冒充公检法",
        "brief": "受害人孙丽接到自称江城市公安局刑侦支队电话，称其身份信息被用于洗钱案件，要求将资金转入“安全账户”接受审查，"
                 "受害人按指示下载视频会议软件并开启屏幕共享，向对方账户转账12万元后发现被骗。",
        "reporter": ("孙丽", "13600000000", "110101199508080000"),
        "location": "城东区望湖路5号",
        "org": "O004",
        "police": "U005",
        "templates": [],
        "answers": {},
        "mode": "none",
        "excluded": [],
        "stage": "intake",
        "elapsed": 0,
        "created_days": 4,
        "start_hour": 10,
        "start_minute": 20,
        "materials": [],
    },
    {
        "id": "RC202611160004",
        "case_no": "JC20261116004",
        "category": "网络婚恋交友",
        "brief": "受害人吴婷通过婚恋网站与一名自称海外高管的男子网恋，双方确立恋爱关系后，对方称有内部渠道可以投资赚钱、"
                 "稳赚不赔，诱导受害人在虚假平台充值。前期小额投入可返利提现，受害人先后在平台充值23万元后无法提现，"
                 "对方随即将其拉黑失联，受害人发现被骗。",
        "reporter": ("吴婷", "13500000000", "110101199303030000"),
        "location": "城东区望湖路33号",
        "org": "O003",
        "police": "U003",
        "templates": [],
        "answers": {},
        "mode": "none",
        "excluded": [],
        "stage": "templates",
        "elapsed": 240,
        "created_days": 2,
        "start_hour": 11,
        "start_minute": 30,
        "materials": [],
    },
    {
        "id": "RC202611160005",
        "case_no": "JC20261116005",
        "category": "冒充电商物流客服",
        "brief": "受害人周敏接到自称快递公司客服电话，称其包裹丢失可三倍赔付，诱导受害人开启屏幕共享并从借贷平台借款后转账2.6万元。",
        "reporter": ("周敏", "13700000000", "110101198806150000"),
        "location": "城东区长虹路12号",
        "org": "O003",
        "police": "U003",
        "templates": [],
        "answers": {},
        "mode": "none",
        "excluded": [],
        "stage": "intake",
        "elapsed": 0,
        "created_days": 3,
        "start_hour": 16,
        "start_minute": 5,
        "materials": [],
    },
    {
        "id": "RC202611160006",
        "case_no": "JC20261116006",
        "category": "虚假征信",
        "brief": "受害人郑浩接到自称银保监会工作人员电话，称其校园贷账户未注销将影响征信，按指示在多个平台贷款后转账“验资”4.2万元。",
        "reporter": ("郑浩", "13400000000", "110101199511110000"),
        "location": "城东区望湖路66号",
        "org": "O004",
        "police": "U005",
        "templates": [],
        "answers": {},
        "mode": "none",
        "excluded": [],
        "stage": "templates",
        "elapsed": 120,
        "created_days": 1,
        "start_hour": 9,
        "start_minute": 45,
        "materials": [],
    },
    {
        "id": "RC202611160007",
        "case_no": "JC20261116007",
        "category": "冒充领导熟人",
        "brief": "受害人刘洋收到自称公司总经理的微信好友申请，对方以“急需支付供应商货款、不便走公账”为由，"
                 "要求受害人先行垫付转账18万元，受害人转账后发现与总经理核对，方知被骗。",
        "reporter": ("刘洋", "13200000000", "110101199704040000"),
        "location": "城东区长虹路3号",
        "org": "O003",
        "police": "U002",
        "templates": [],
        "answers": {},
        "mode": "none",
        "excluded": [],
        "stage": "intake",
        "elapsed": 0,
        "created_days": 5,
        "start_hour": 14,
        "start_minute": 10,
        "materials": [],
    },
    {
        "id": "RC202611160008",
        "case_no": "JC20261116008",
        "category": "网络游戏虚假交易",
        "brief": "受害人在游戏内看到低价出售稀有装备的广告，添加对方QQ后通过第三方链接支付1.1万元，"
                 "对方以“账号冻结需解冻保证金”为由要求继续付款，受害人发现被骗。",
        "reporter": ("陈杰", "13300000000", "110101200001010000"),
        "location": "城东区望湖路9号",
        "org": "O003",
        "police": "U003",
        "templates": [],
        "answers": {},
        "mode": "none",
        "excluded": [],
        "stage": "templates",
        "elapsed": 90,
        "created_days": 8,
        "start_hour": 15,
        "start_minute": 40,
        "materials": [],
    },
    {
        "id": "RC202611160009",
        "case_no": "JC20261116009",
        "category": "虚假中奖",
        "brief": "受害人收到“购物抽奖中奖”短信，按提示添加客服后被告知需先缴纳个人所得税与保证金才能领奖，"
                 "先后转账9800元后发现对方失联。",
        "reporter": ("马丽", "13100000000", "110101199909090000"),
        "location": "城东区长虹路45号",
        "org": "O003",
        "police": "U002",
        "templates": [],
        "answers": {},
        "mode": "none",
        "excluded": [],
        "stage": "intake",
        "elapsed": 0,
        "created_days": 7,
        "start_hour": 10,
        "start_minute": 5,
        "materials": [],
    },
    {
        "id": "RC202611160010",
        "case_no": "JC20261116010",
        "category": "虚假网络贷款",
        "brief": "受害人下载“速贷宝”APP申请贷款，对方以银行卡号填写错误需缴纳解冻费、保证金为由，"
                 "诱导受害人先后转账3.6万元仍未放款。",
        "reporter": ("何强", "13000000000", "110101198501010000"),
        "location": "城东区望湖路18号",
        "org": "O003",
        "police": "U003",
        "templates": [],
        "answers": {},
        "mode": "none",
        "excluded": [],
        "stage": "intake",
        "elapsed": 0,
        "created_days": 6,
        "start_hour": 11,
        "start_minute": 15,
        "materials": [],
    },
    {
        "id": "RC202611160011",
        "case_no": "JC20261116011",
        "category": "校园贷培训贷",
        "brief": "在校学生被“培训贷”中介诱导，在多个平台贷款2.8万元用于购买课程，课程未开课机构失联，"
                 "贷款仍需偿还，学生报案求助。",
        "reporter": ("许诺", "18800000000", "110101200203030000"),
        "location": "城东区长虹路大学城",
        "org": "O003",
        "police": "U002",
        "templates": [],
        "answers": {},
        "mode": "none",
        "excluded": [],
        "stage": "templates",
        "elapsed": 60,
        "created_days": 3,
        "start_hour": 14,
        "start_minute": 50,
        "materials": [],
    },
    {
        "id": "RC202611160012",
        "case_no": "JC20261116012",
        "category": "虚假购物服务",
        "brief": "受害人在社交平台看到低价代购手机广告，支付6800元后对方以“海关扣货需补税”为由继续索要款项，"
                 "受害人拒绝后被拉黑。",
        "reporter": ("唐薇", "18900000000", "110101199606060000"),
        "location": "城东区望湖路77号",
        "org": "O003",
        "police": "U003",
        "templates": [],
        "answers": {},
        "mode": "none",
        "excluded": [],
        "stage": "intake",
        "elapsed": 0,
        "created_days": 0,
        "start_hour": 9,
        "start_minute": 5,
        "materials": [],
    },
]


# ------------------------------------------------------------------ 初始化
def _seed_orgs_users() -> None:
    with db.write() as conn:
        for org_id, name, parent, level, code, sort_no in ORGS:
            conn.execute(
                "INSERT OR REPLACE INTO org (id, name, parent_id, level, code, sort_no) VALUES (?, ?, ?, ?, ?, ?)",
                (org_id, name, parent, level, code, sort_no),
            )
        for user_id, badge, name, role, org_id, title in USERS:
            conn.execute(
                "INSERT OR REPLACE INTO user (id, badge_no, name, password_hash, role, org_id, title, status, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?)",
                (user_id, badge, name, security.hash_password(DEMO_PASSWORD), role, org_id, title, db.now_iso()),
            )


def _seed_external_cases() -> None:
    with db.write() as conn:
        for row in EXTERNAL_CASES:
            conn.execute(
                "INSERT OR REPLACE INTO external_case (id, system, case_no, case_category, brief, reporter_name, "
                "reporter_phone, unit, report_time, location, officer, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '受案')",
                row,
            )


def _record_dt(days_ago: int, hour: int, minute: int) -> datetime:
    base = datetime.now() - timedelta(days=days_ago)
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _seed_records() -> None:
    for spec in RECORD_SPECS:
        created_dt = _record_dt(spec["created_days"], spec["start_hour"], spec["start_minute"])
        elapsed = int(spec["elapsed"])
        if spec["stage"] == "completed":
            completed_dt = created_dt + timedelta(seconds=elapsed)
            updated = completed_dt
        elif spec["stage"] == "inquiry":
            updated = created_dt + timedelta(seconds=elapsed)
        else:
            updated = created_dt + timedelta(minutes=6)
        reporter_name, reporter_phone, reporter_id = spec["reporter"]
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO record (id, case_no, case_category, brief, reporter_name, reporter_phone, "
                "reporter_id_no, report_time, case_location, unit, org_id, police_user_id, stage, "
                "selected_templates, flow_excluded, elapsed_sec, snapshot, created_at, updated_at, completed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec["id"], spec["case_no"], spec["category"], spec["brief"], reporter_name, reporter_phone,
                    reporter_id, _fmt(created_dt - timedelta(minutes=25)), spec["location"],
                    _org_name(spec["org"]), spec["org"], spec["police"], spec["stage"],
                    db.json_dump(spec["templates"]), db.json_dump(spec["excluded"]), elapsed,
                    db.json_dump({"saved_at": _fmt(updated), "scroll_anchor": {"section": "process", "index": 3}}),
                    _fmt(created_dt), _fmt(updated),
                    _fmt(completed_dt) if spec["stage"] == "completed" else None,
                ),
            )
        if spec["templates"] and spec["mode"] != "none":
            _seed_outline(spec)
        if spec["materials"]:
            _seed_materials(spec)


def _org_name(org_id: str) -> str:
    row = db.one("SELECT name FROM org WHERE id = ?", (org_id,))
    return row["name"] if row else ""


def _default_answer(section_key: str, index: int) -> str:
    pool = DEFAULT_ANSWERS.get(section_key) or [""]
    return pool[index % len(pool)]


def _seed_outline(spec: dict) -> None:
    merged = templates_svc.merge_outline(spec["templates"])
    if not merged["sections"]:
        print(f"  [warn] 模板未就绪，{spec['case_no']} 未能生成问询大纲")
        return
    excluded = set(spec["excluded"])
    answers = spec["answers"]
    now = _fmt(_record_dt(spec["created_days"], spec["start_hour"], spec["start_minute"]))
    with db.write() as conn:
        for section in merged["sections"]:
            sort_no = 0
            for index, question in enumerate(section["questions"]):
                flow = question.get("flow")
                if flow and flow in excluded:
                    continue
                field = question.get("field")
                if field:
                    answer = answers.get(field, "")
                else:
                    answer = _default_answer(section["key"], index)
                if spec["mode"] == "partial":
                    if section["key"] == "evidence" and index >= 2:
                        answer = ""
                    if field in ("login_ip", "serial_no"):
                        answer = answers.get(field, "")
                conn.execute(
                    "INSERT OR REPLACE INTO qa_item (id, record_id, section, question, answer, source, flow, field, "
                    "sort_no, is_deleted, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'template', ?, ?, ?, 0, ?, ?)",
                    (db.new_id("QA", 12), spec["id"], section["key"], question["text"], answer,
                     flow, field, sort_no, now, now),
                )
                sort_no += 1


def _seed_materials(spec: dict) -> None:
    target_dir = MATERIAL_DIR / spec["id"]
    target_dir.mkdir(parents=True, exist_ok=True)
    upload_dt = _record_dt(spec["created_days"], spec["start_hour"], spec["start_minute"]) + timedelta(minutes=18)
    for idx, (name, note) in enumerate(spec["materials"]):
        path = target_dir / f"demo{idx + 1:02d}_{name}"
        if not path.exists():
            _draw_material(path, kind="transfer" if "转账" in name else "chat",
                           title=name.replace(".png", ""), subtitle=note)
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        with db.write() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO material (id, record_id, file_name, stored_name, size, mime, sha256, "
                "uploader_id, note, created_at) VALUES (?, ?, ?, ?, ?, 'image/png', ?, ?, ?, ?)",
                (db.new_id("MT", 10), spec["id"], name, path.name, len(data), digest,
                 spec["police"], note, _fmt(upload_dt)),
            )


# ------------------------------------------------------------------ 材料图片
def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    candidates = [
        r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _draw_material(path: Path, kind: str, title: str, subtitle: str) -> None:
    """生成虚构的演示材料截图（转账凭证 / 聊天记录），并带有明显水印。"""
    from PIL import Image, ImageDraw

    width, height = 720, 1180
    image = Image.new("RGB", (width, height), (240, 242, 246))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 92], fill=(255, 255, 255))
    draw.text((28, 30), title, font=_font(30, True), fill=(32, 36, 44))
    draw.text((width - 110, 36), "演示", font=_font(22), fill=(150, 155, 165))

    if kind == "transfer":
        draw.rounded_rectangle([24, 116, width - 24, 470], 18, fill=(255, 255, 255))
        draw.text((56, 152), "转账成功", font=_font(34, True), fill=(28, 160, 96))
        draw.text((56, 214), "¥ 300,000.00", font=_font(52, True), fill=(24, 28, 36))
        rows = [("收款方", "建设银行 尾号 0001"), ("收款户名", "（未记录）"),
                ("转账时间", "2026-10-18 14:06"), ("转账方式", "手机银行"),
                ("付款卡号", "工商银行 6222****1234"), ("交易流水号", "202610181406000001")]
        y = 300
        for label, value in rows:
            draw.text((56, y), label, font=_font(20), fill=(130, 136, 148))
            draw.text((210, y), value, font=_font(22), fill=(38, 42, 50))
            y += 34
        draw.rounded_rectangle([24, 490, width - 24, 560], 16, fill=(255, 246, 235))
        draw.text((52, 512), "提示：对方要求继续转账“解冻保证金”3 万元", font=_font(20), fill=(190, 100, 20))
    else:
        draw.rounded_rectangle([24, 116, width - 24, 260], 18, fill=(255, 255, 255))
        draw.text((52, 148), "导师-国盛张老师", font=_font(24, True), fill=(32, 36, 44))
        draw.text((52, 194), "（该账号已注销）", font=_font(18), fill=(160, 165, 175))
        bubbles = [
            ("左", "老师带你做，今晚这波稳赚，先把资金转到指定账户"),
            ("右", "我已经转了30万，平台什么时候能提现？"),
            ("左", "要完成总额80万的入金任务，系统才开放提现通道"),
            ("右", "那我把剩下的也转过去"),
            ("左", "对，抓紧时间，名额只有今天"),
        ]
        y = 300
        for side, text in bubbles:
            if side == "左":
                draw.rounded_rectangle([40, y, 520, y + 76], 16, fill=(255, 255, 255))
                draw.text((62, y + 20), text[:22], font=_font(20), fill=(40, 44, 52))
                if len(text) > 22:
                    draw.text((62, y + 46), text[22:], font=_font(20), fill=(40, 44, 52))
            else:
                draw.rounded_rectangle([200, y, width - 40, y + 56], 16, fill=(149, 236, 105))
                draw.text((222, y + 16), text, font=_font(20), fill=(28, 32, 36))
            y += 100
        draw.rounded_rectangle([24, y + 10, width - 24, y + 80], 16, fill=(255, 240, 240))
        draw.text((52, y + 32), "聊天记录仅为虚构演示数据，不含真实人员信息", font=_font(20), fill=(190, 60, 60))

    # 斜向水印
    watermark = Image.new("RGBA", image.size, (255, 255, 255, 0))
    wm_draw = ImageDraw.Draw(watermark)
    wm_draw.text((90, 700), "虚构演示数据 · 禁止作为真实案件材料", font=_font(30), fill=(200, 60, 60, 70))
    watermark = watermark.rotate(24, expand=False)
    image = Image.alpha_composite(image.convert("RGBA"), watermark).convert("RGB")
    draw = ImageDraw.Draw(image)
    draw.text((24, height - 46), f"{subtitle}｜智能问询笔录平台 演示材料", font=_font(16), fill=(150, 155, 165))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, "PNG")


# ------------------------------------------------------------------ 历史笔录样例
def _seed_history_samples() -> None:
    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    source = DEMO_DIR / "history_samples.json"
    if not source.exists():
        print("  [warn] 未找到 data/demo/history_samples.json，跳过历史笔录样例生成")
        return
    samples = json.loads(source.read_text(encoding="utf-8"))
    from docx import Document
    from docx.shared import Pt

    for idx, sample in enumerate(samples, start=1):
        doc = Document()
        style = doc.styles["Normal"]
        style.font.name = "仿宋"
        style.font.size = Pt(12)
        doc.add_heading(sample["name"], level=1)
        for line in sample["text"].splitlines():
            if line.strip():
                doc.add_paragraph(line.strip())
        target = DEMO_DIR / f"历史笔录样例{idx}_{sample['name']}.docx"
        doc.save(str(target))
        print(f"  [history] 已生成 {target.name}")


# ------------------------------------------------------------------ 审计样例
def _seed_audit() -> None:
    user = {"id": "U002", "badge_no": "100101", "org_id": "O003", "name": "张伟"}
    now = datetime.now()
    entries = [
        ("login", "user", "U002", {"result": "success"}, 6, 9, 5),
        ("record.create", "record", "RC202611160001", {"case_no": "JC20261116001"}, 6, 9, 12),
        ("record.sync_external", "record", "RC202611160001",
         {"system": "zhiyanpan", "case": "ZYP2026-1016-0031"}, 6, 9, 15),
        ("record.template_select", "record", "RC202611160001", {"template_ids": ["T03", "T00"]}, 6, 9, 21),
        ("qa.update", "record", "RC202611160001", {"fields": ["answer"]}, 6, 9, 33),
        ("advice.adopt", "record", "RC202611160001",
         {"question": "请明确对方收款银行卡号（或支付账号）的完整号码，如有多张请逐张说明。"}, 6, 9, 41),
        ("material.upload", "record", "RC202611160001", {"file_name": "转账凭证_30万元.png"}, 6, 9, 48),
        ("record.save", "record", "RC202611160001", {"elapsed_sec": 1200}, 6, 9, 52),
        ("document.export", "record", "RC202611160002", {"case_no": "JC20261116002"}, 9, 10, 8),
        ("import.parse", "import_job", "IJ-DEMO", {"filename": "历史笔录样例1.docx"}, 9, 10, 30),
    ]
    for action, target_type, target_id, detail, days, hour, minute in entries:
        stamp = (now - timedelta(days=days)).replace(hour=hour, minute=minute, second=12, microsecond=0)
        audit.write(user, action, target_type, target_id, detail,
                    created_at=stamp.strftime("%Y-%m-%dT%H:%M:%S"))


# ------------------------------------------------------------------ 入口
def seed_all(force: bool = False) -> None:
    print("=" * 60)
    print("初始化演示数据（全部为虚构脱敏数据）")
    print("=" * 60)
    db.init_db(force=True)
    _seed_orgs_users()
    _seed_external_cases()
    count = templates_svc.sync_from_files()
    print(f"  模板同步：{count} 个")
    _seed_history_samples()
    _seed_records()
    _seed_audit()
    audit.reset_cache()
    stats = {
        "orgs": db.scalar("SELECT COUNT(*) FROM org", (), 0),
        "users": db.scalar("SELECT COUNT(*) FROM user", (), 0),
        "records": db.scalar("SELECT COUNT(*) FROM record", (), 0),
        "qa": db.scalar("SELECT COUNT(*) FROM qa_item WHERE is_deleted = 0", (), 0),
        "materials": db.scalar("SELECT COUNT(*) FROM material", (), 0),
        "external": db.scalar("SELECT COUNT(*) FROM external_case", (), 0),
    }
    print(f"  机构 {stats['orgs']} / 账号 {stats['users']} / 笔录 {stats['records']} / "
          f"问答 {stats['qa']} / 材料 {stats['materials']} / 外部案件 {stats['external']}")
    print("  演示账号：100001 管理员 / 100101 办案民警 / 100201 研判员，密码均为 123456")
    print("=" * 60)


if __name__ == "__main__":
    seed_all(force=True)
