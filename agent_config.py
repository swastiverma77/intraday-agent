import os

BREEZE_API_KEY    = os.getenv("BREEZE_API_KEY", "YOUR_API_KEY_HERE")
BREEZE_API_SECRET = os.getenv("BREEZE_API_SECRET", "YOUR_API_SECRET_HERE")

ICICI_USER_ID   = os.getenv("ICICI_USER_ID", "YOUR_ICICI_USER_ID")
ICICI_PASSWORD  = os.getenv("ICICI_PASSWORD", "YOUR_ICICI_PASSWORD")
ICICI_DOB       = os.getenv("ICICI_DOB", "DD-MM-YYYY")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "YOUR_CHAT_ID_HERE")

CAPITAL_PER_TRADE   = float(os.getenv("CAPITAL_PER_TRADE", "50000"))
RISK_REWARD_RATIO   = 2.0
RISK_PER_TRADE      = float(os.getenv("RISK_PER_TRADE", "1000"))
MAX_TRADES          = int(os.getenv("MAX_TRADES", "3"))
MAX_GAP_PERCENT     = 2.0
MAX_STOCKS_PER_SECTOR = 2
NUM_SECTORS         = 2
TRADE_CUTOFF_TIME   = "10:30"

PRE_MARKET_TIME     = "09:10"
MARKET_OPEN_TIME    = "09:15"
STOCK_PICK_TIME     = "09:25"
CANDLE_BASE_TIME    = "09:30"
SIGNAL_SCAN_START   = "09:30"
CUTOFF_TIME         = "10:30"

NIFTY50_FNO_STOCKS = [
    "RELIND", "TCS", "HDFBAN", "INFTEC", "ICIBAN",
    "HINLEV", "ITC", "STABAN", "BHAAIR", "KOTMAH",
    "LARTOU", "AXIBAN", "ASIPAI", "MARUTI", "HCLTEC",
    "SUNPHA", "TITIND", "BAJFI", "WIPRO", "NESIND",
    "POWGRI", "NTPC", "TECMAH", "ONGC", "TATMOT",
    "ADAENT", "JSWSTE", "BAFINS", "COALIN", "DRREDD",
    "DIVLAB", "CIPLA", "EICMOT", "HERHON", "INDBA",
    "BRIIND", "GRASIM", "HINDAL", "TATGLO", "APOHOS",
    "ADAPOR", "BHAPET", "TATSTE", "SBILIF", "HDFSTA",
    "UNIP", "BAAUTO", "MAHMAH", "LTIM", "ZOMATO",
    "HDFAMC", "PIDIND", "ODICEM", "TRENT", "TVSMOT",
    "DIXTEC", "PERSYS", "NIITEC", "POLI", "CHOINV",
    "MUTFIN", "GODCON", "GODPRO", "DLFLIM", "INDHOT",
    "INDRAI", "INFEDG", "PAGIND", "SIEMEN", "HINAER",
    "BHAELE", "ORAFIN", "MPHLIM", "LTTEC", "BANBAR",
    "CANBAN", "PUNBAN", "UNIBAN", "IDFBAN", "FEDBAN",
    "RBLBAN", "BANBAN", "VEDLIM", "NATMIN", "SAIL",
    "NATALU", "HINCOP", "JINSP", "JINSTA", "TATCHE",
    "TATCOM", "TATELX", "TATPOW", "GAIL", "INDOIL",
    "HINPET", "PETLNG", "INDGAS", "MAHGAS", "GUJGA",
    "ADAGAS", "RURELE", "POWFIN", "CONCOR", "ADAGRE",
    "ADAPOW", "ADATRA", "ADICAP", "ADIFAS", "ACC",
    "AURPHA", "BIOCON", "BOSLIM", "BALIND", "BATIND",
    "BERPAI", "COLPAL", "DABIND", "MARLIM", "VOLTAS",
    "CROGR", "DEENIT", "NAVFLU", "PIIND", "SRF",
    "DRLAL", "METHEA", "SYNINT", "LAULAB", "IPCLAB",
    "GLEPHA", "TORPHA", "CADHEA", "ALKLAB", "OBEREA",
    "PVRLIM", "DELCOR", "JUBFOO", "INDCEM", "RAMCEM",
    "SHRCEM", "ULTCEM", "JKCEME", "BHAINF", "SBICAR",
    "ICILOM", "ICIPRU", "MAXFIN", "SUNTV", "ZEEENT",
    "MCX", "INDEN", "ESCORT", "MOTSUM", "EXIIND",
    "MRFTYR", "GNFC", "GSPL", "CITUNI", "RAIIND",
    "CUMIND", "TORPOW", "UNIBR", "INDMAR",
]

SECTOR_INDICES = {
    "IT":        "CNXIT",
    "Bank":      "CNXBANK",
    "Auto":      "CNXAUTO",
    "FMCG":      "CNXFMCG",
    "Pharma":    "CNXPHARMA",
    "Metal":     "CNXMETAL",
    "Energy":    "CNXENERGY",
    "Realty":    "CNXREALTY",
    "Financial": "CNXFIN",
    "Media":     "CNXMEDIA",
    "Infra":     "CNXINFRA",
    "PSU Bank":  "CNXPSUBNK",
}

SECTOR_STOCKS = {
    "IT":        ["TCS", "INFTEC", "HCLTEC", "WIPRO", "TECMAH"],
    "Bank":      ["HDFBAN", "ICIBAN", "STABAN", "KOTMAH", "AXIBAN", "INDBA"],
    "Auto":      ["MARUTI", "TATMOT", "BAAUTO", "HERHON", "EICMOT", "MAHMAH"],
    "FMCG":      ["HINLEV", "ITC", "NESIND", "BRIIND", "TATGLO"],
    "Pharma":    ["SUNPHA", "DRREDD", "DIVLAB", "CIPLA", "APOHOS"],
    "Metal":     ["TATSTE", "JSWSTE", "HINDAL", "COALIN", "SAIL", "VEDLIM", "HINCOP", "NATMIN"],
    "Energy":    ["RELIND", "ONGC", "BHAPET", "ADAENT"],
    "Realty":    ["DLFLIM", "GODPRO", "OBEREA", "PVRLIM"],
    "Financial": ["BAJFI", "BAFINS", "SBILIF", "HDFSTA"],
    "Infra":     ["LARTOU", "POWGRI", "NTPC"],
}

CHROME_DRIVER_PATH = os.getenv("CHROME_DRIVER_PATH", "/snap/bin/chromium.chromedriver")
HEADLESS_BROWSER    = True

LOG_FILE   = "logs/agent.log"
STATE_FILE = "state/daily_state.json"
