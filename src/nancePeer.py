import webbrowser, threading, ipaddress, datetime, requests, sqlite3, socket, random, base64, struct, uuid, time, yaml, json, stun, copy, sys, os
from flask import Flask, render_template, request, redirect, url_for
import traceback

os.makedirs("config", exist_ok=True)
os.makedirs("logs", exist_ok=True)

logConfig = {
    "version": 1,
    "disable_existing_loggers": True,
    "formatters": {
        "detailed": {
            "format": "%(asctime)s %(name)s %(levelname)s %(message)s"
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "formatter": "detailed",
            "stream": "ext://sys.stdout"
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "level": "DEBUG",
            "formatter": "detailed",
            "filename": "logs/NanceChat.log",
            "maxBytes": 1048576,
            "backupCount": 3
        }
    },
    "loggers": {
        "__main__": {
            "level": "DEBUG",
            "handlers": ["console", "file"],
            "propagate": False
        },
        "settings": {
            "level": "DEBUG",
            "handlers": ["console", "file"],
            "propagate": False
        }
    },
    "root": {
        "level": "DEBUG",
        "handlers": ["console", "file"]
    }
}
os.makedirs("dbs", exist_ok=True)

settings:dict = {}

from logging import getLogger, config
logger = getLogger(__name__)
config.dictConfig(logConfig)

conn = sqlite3.connect("dbs/NanceChats.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS chats (
        uuid TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        uuid TEXT PRIMARY KEY,
        chatUuid TEXT NOT NULL,
        name TEXT NOT NULL,
        content TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS peers (
        uuid TEXT PRIMARY KEY,
        ip TEXT NOT NULL,
        port INTEGER NOT NULL,
        natConeType TEXT NOT NULL,
        isLocalIp INTEGER NOT NULL
    )
""")
conn.commit()

onceConnectedIps:list[str] = []
onceConnectedIpsLock = threading.Lock()
custodyChats:dict[list[dict]] = {}
custodyChatsLock = threading.Lock()
reses:dict[any] = {}
resesLock = threading.Lock()
requestNum:int = 0
requestNumLock = threading.Lock()

info = {"natConeType":"", "ip":"", "port":0}
sock = None
settings:dict[any] = {}

class Settings:
    settings = None
    def __init__(self, settingsFile:str="config/settings.json", port:int=34521):
        if os.path.isfile(settingsFile):
            self.settings = json.load(open(settingsFile, encoding="utf-8"))
            logger.info("Config loaded")
            if self.settings["messagesMaxSize"] > 819200:
                logger.error("The messagesMaxSize cannot be larger than 819200 bytes.")
                self.settings["messagesMaxSize"] = 819200
        else:
            self.settings = {
                "messagesMaxSize":819200,
                "port":port,
                "bootstrapPeers": [{"ip":"59.157.6.33", "port":8080}],
                "stunServers": [
                    {"ip":"stun.l.google.com", "port":19302}, 
                    {"ip":"stun.l.google.com", "port":5349},
                    {"ip":"stun1.l.google.com", "port":3478},
                    {"ip":"stun1.l.google.com", "port":5349},
                    {"ip":"stun2.l.google.com", "port":19302},
                    {"ip":"stun2.l.google.com", "port":5349},
                    {"ip":"stun3.l.google.com", "port":3478},
                    {"ip":"stun3.l.google.com", "port":5349},
                    {"ip":"stun4.l.google.com", "port":19302},
                    {"ip":"stun4.l.google.com", "port":5349}
                ],
                "amIBootstrapPeer":False
            }
            json.dump(self.settings, open(settingsFile, encoding="utf-8", mode="w"), indent=4)
            logger.info("Config saved")

class Utils:
    def openBrowser(url:str):
        time.sleep(3)
        webbrowser.open(url)
    def allDeleteMessagesAndChats():
        cursor.execute("DELETE FROM chats")
        conn.commit()
        cursor.execute("DELETE FROM messages")
        conn.commit()
    def removeDuplicates(dicts:list[dict], key:str):
        seen = {}
        for d in dicts:
            seen[d[key]] = d
        return list(seen.values())
    def checkAndMergeStunDatas(newPeerDs:list[dict]):
        okNewPeerDs:list[dict] = Utils.removeDuplicates(newPeerDs, "ip")
        for newPeerD in okNewPeerDs:
            if Utils.isMyIp(newPeerD["ip"]):
                continue
            elif not (newPeerD["natConeType"] in ["Restricted Cone", "Full Cone"] or Utils.isLocalIp(newPeerD["ip"])):
                continue
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(6)
                try:
                    message = {
                        "m":"p"
                    }
                    sock.sendto(base64.b64encode(json.dumps(message).encode("utf-8")),(newPeerD["ip"], newPeerD["port"]))
                    while True:
                        content, addr = sock.recvfrom(1024)
                        if addr[0] == newPeerD["ip"]:
                            break
                    content = json.loads(base64.b64decode(content))
                    if not (content["m"] == "R" and content["r"] == 0):
                        continue
                except Exception as e:
                    logger.error(f"Error in checkAndMergeStunDatas:{e}")
                    continue
                cursor.execute("DELETE FROM peers WHERE ip=? AND port=?", (newPeerD["ip"], newPeerD["port"]))
                conn.commit()
                cursor.execute("INSERT INTO peers (uuid, ip, port, natConeType, isLocalIp) VALUES (?, ?, ?, ?, ?)",(str(uuid.uuid4()), newPeerD["ip"], newPeerD["port"], newPeerD["natConeType"], 1 if Utils.isLocalIp(newPeerD["ip"]) else 0))
                conn.commit()
    def isPortAvailable(port):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            result = sock.connect_ex(("127.0.0.1", port))
            return result != 0
    def checkPorts(x, y):
        for port in range(x, y+1):
            if Utils.isPortAvailable(port):
                return True
            else:
                return False
    def getStun(ip:str, port:int):
        try:
            natType, ip, port = stun.get_ip_info(stun_host=ip, stun_port=port)
            logger.debug(f"Get stun info: {natType} - {ip}:{port}")
            return natType, ip, port
        except Exception as e:
            logger.error(f"Exception(get STUN): {traceback.format_exc()}")
        return None, None, None
    def isLocalIp(ipAddress):
        localIpv4 = [
            "127.0.0.1",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16"
        ]
        localIpv6 = [
            "::1",
            "fc00::/7",
            "fe80::/10"
        ]
        try:
            ip = ipaddress.ip_address(ipAddress)
            if isinstance(ip, ipaddress.IPv4Address):
                for local in localIpv4:
                    if "/" in local:
                        network = ipaddress.ip_network(local, strict=False)
                        if ip in network:
                            return True
                    elif ip == ipaddress.ip_address(local):
                        return True
            if isinstance(ip, ipaddress.IPv6Address):
                for local in localIpv6:
                    if "/" in local:
                        network = ipaddress.ip_network(local, strict=False)
                        if ip in network:
                            return True
                    elif ip == ipaddress.ip_address(local):
                        return True
        except ValueError:
            return False
        return False
    def getPublicIp():
        content = requests.get("https://api.ipify.org")
        return content.text
    def getLocalIp():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            localIp = sock.getsockname()[0]
        return localIp
    def isMyIp(ip):
        myPublicIp = Utils.getPublicIp()
        myLocalIp = Utils.getLocalIp()
        return ip == myPublicIp or ip == myLocalIp or ipaddress.ip_address(ip).is_loopback

class Server:
    def __init__(self, request:dict, requestId:str, addr):
        self.request = request
        self.requestId = requestId
        self.addr = addr
    def send(self, message:dict):
        if self.requestId:
            message["z"] = self.requestId
        sock.sendto(base64.b64encode(json.dumps(message).encode("utf-8")), self.addr)
    def reqChatList(self, maxChatsLength:int):
        if not maxChatsLength:
            message = {
                "m": "R",
                "r": 2,
                "c": {}
            }
            self.send(message)
            return
        cursor.execute("SELECT * FROM chats")
        chats = cursor.fetchall()
        chatsDs:list[dict] = []
        for chat in random.sample(chats, min(len(chats), maxChatsLength)):
            chatsDs.append({"uuid": chat[0], "name": chat[1], "timestamp": chat[2]})
        message = {
            "m":"R",
            "r":0,
            "c":{
                "chats":random.sample(chatsDs, min(len(chatsDs), maxChatsLength))
            }
        }
        self.send(message)
    def reqMessages(self, chatUuid:str):
        if not chatUuid:
            message = {
                "m": "R",
                "r": 2,
                "c": {}
            }
            self.send(message)
            return
        cursor.execute("SELECT * FROM messages WHERE chatUuid=?", (chatUuid,))
        messages = cursor.fetchall()
        if messages:
            messagesDs:list[dict] = [{"uuid":message[0], "name":message[2], "content":message[3], "timestamp":message[4]} for message in messages]
            message = {
                "m":"R",
                "r":0,
                "c":{
                    "messages":messagesDs
                }
            }
        else:
            message = {
                "m":"R",
                "r":10,
            }
        self.send(message)
    def reqPeersList(self, maxPeersLength:int):
        cursor.execute("SELECT * FROM peers")
        peers = cursor.fetchall()
        peersDs:list[dict] = []
        isLocalIp = Utils.isLocalIp(self.addr[0])
        if not maxPeersLength:
            message = {
                "m": "R",
                "r": 2,
                "c": {}
            }
            self.send(message, self.addr)
            return
        for peer in random.sample(peers, min(len(peers), maxPeersLength)):
            if peer[4]:
                if not isLocalIp:
                    continue
            if len(base64.b64encode(json.dumps({"m":"R","r":0,"c":{"peers":peersDs+[{"ip": peer[1], "port": peer[2], "natConeType": peer[3]}]}}).encode("utf-8"))) > 819200:
                break
            peersDs.append({"ip": peer[1], "port": peer[2], "natConeType": peer[3]})
        message = {
            "m":"R",
            "r":0,
            "c":{
                "peers":peersDs
            }
        }
        self.send(message)
    def registerPeer(self, peerData):
        natConeType = peerData.get("natConeType")
        isLocalIp = Utils.isLocalIp(self.addr[0])
        if not natConeType in ["Restricted Cone", "Full Cone"]:
            message = {
                "m": "R",
                "r": 2,
                "c": {}
            }
            self.send(message)
            return
        client = Client(self.addr[0], self.addr[1])
        if client.ping():
            uuidValue = str(uuid.uuid4())
            cursor.execute("DELETE FROM peers WHERE ip=? AND port=?", (self.addr[0], self.addr[1]))
            conn.commit()
            cursor.execute("INSERT INTO peers (uuid, ip, port, natConeType, isLocalIp) VALUES (?, ?, ?, ?, ?)",(uuidValue, self.addr[0], self.addr[1], natConeType, 1 if isLocalIp else 0))
            conn.commit()
            message = {
                "m": "R",
                "r": 0,
                "c": {}
            }
            logger.info(f"Registered peer: {self.addr[0]}:{self.addr[1]}")
        else:
            logger.warning(f"Failed to connect to peer: {self.addr[0]}:{self.addr[1]}")
            message = {
                "m": "R",
                "r": 3,
                "c": {}
            }
        self.send(message)
    def sendMessage(self, messageData):
        chatUuid = messageData["chatUuid"]
        name = messageData["name"]
        content = messageData["content"]
        if not (chatUuid and name and content):
            message = {
                "m": "R",
                "r": 2,
                "c": {}
            }
            self.send(message)
            return
        cursor.execute("SELECT * FROM messages WHERE chatUuid=?", (chatUuid,))
        messages = cursor.fetchall()
        if not messages:
            message = {
                "m":"R",
                "r":10,
            }
        messages:list[dict] = [{"uuid":message[0], "name":message[2], "content":message[3], "timestamp":message[4]} for message in messages]
        messageUuid = str(uuid.uuid4())
        timestamp = str(datetime.datetime.timestamp(datetime.datetime.now()))
        messages.append({"messageUuid":messageUuid, "chatUuid":chatUuid, "name":name, "content":content, "timestamp":timestamp})
        if not len(base64.b64encode(json.dumps(messages).encode("utf-8"))) < settings["messagesMaxSize"]:
            message = {
                "m": "R",
                "r": 20,
                "c": {}
            }
            self.send(message)
            return
        cursor.execute("INSERT INTO messages (uuid, chatUuid, name, content, timestamp) VALUES (?, ?, ?, ?, ?)", (messageUuid, chatUuid, name, content, timestamp))
        conn.commit()
        message = {
            "m": "R",
            "r": 0,
            "c": {}
        }
        self.send(message)

class Client:
    def __init__(self, ip:str, port:int):
        self.ip = ip
        self.port = port
        self.requestId = None
        self.sendedMessage = None
    def findRes(self):
        global reses, requestNum
        global resesLock, requestNumLock
        for _ in range(20):
            with resesLock:
                nowReses = reses.copy()
            targetReses:list[dict] = []
            if nowReses.get(self.requestId):
                targetReses.append(nowReses[self.requestId])
                with resesLock, requestNumLock:
                    reses.pop(self.requestId)
                    requestNum -= 1
                return targetReses[0]
            time.sleep(0.5)
        return {"m":"R", "r":90,"c":{}}
    def send(self, message:dict):
        global requestNum
        global requestNumLock
        with requestNumLock:
            requestNum += 1
        message["z"] = str(uuid.uuid4())
        self.requestId = message["z"]
        sock.sendto(base64.b64encode(json.dumps(message).encode("utf-8")), (self.ip, self.port))
        self.sendedMessage = message
        return self.requestId
    def ping(self):
        try:
            message = {
                "m":"p"
            }
            self.send(message)
            res = self.findRes()
            if res["m"] == "R" and res["r"] == 0:
                return True
        except:
            pass
        return False
    def getChats(self, maxChats=20):
        chats = []
        message = {
            "m":"r",
            "t":"g",
            "d":"c",
            "a":{
                "maxChatsLength":maxChats,
            }
        }
        try:
            self.send(message)
            res = self.findRes()
            if res["m"] == "R" and res["r"] == 0:
                chats = res["c"]["chats"]
                logger.debug(f"Get chats From:{self.ip}")
            else:
                logger.warning(f"Failed get chats From:{self.ip}")
                return None
        except Exception as e:
            logger.error(f"{e}")
            return None
        return chats
    def getMessages(self, chatUuid:str):
        message = {
            "m":"r",
            "t":"g",
            "d":"m",
            "a":{
                "chatUuid":chatUuid
            }
        }
        try:
            self.send(message)
            res = self.findRes()
            if res["m"] == "R" and res["r"] == 0:
                logger.debug(f"Get messages from:{self.ip}")
                return res["c"]["messages"]
        except Exception as e:
            logger.error(f"{e}")
        return None
    def getPeers(self, maxPeers=20):
        message = {
            "m":"r",
            "t":"g",
            "d":"i",
            "a":{
                "maxPeersLength":maxPeers
            }
        }
        try:
            self.send(message)
            res = self.findRes()
            if res["m"] == "R" and res["r"] == 0:
                Utils.checkAndMergeStunDatas(res["c"]["peers"])
                logger.debug(f"Get peers From:{self.ip}")
            else:
                logger.warning(f"Failed get peers From:{self.ip}")
        except Exception as e:
            logger.error(f"{e}")
    def registerPeer(self):
        natConeType = info["natConeType"]
        if not natConeType in ["Restricted Cone", "Full Cone"]:
            return True
        message = {
            "m":"r",
            "t":"r",
            "d":"i",
            "a":{
                "natConeType":natConeType,
            }
        }
        try:
            self.send(message)
            res = self.findRes()
            if res["m"] == "R" and res["r"] == 0:
                logger.info(f"Register I:{ip}:{port} Server:{self.ip}:{self.port}")
                return True
            else:
                logger.warning(f"Failed Register I:{ip}:{port} Server:{self.ip}:{self.port}")
        except Exception as e:
            logger.error(f"{traceback.format_exc()}")
        return False
    def sendMessage(self, chatUuid:str, name:str, res:str):
        message = {
            "m":"r",
            "t":"s",
            "d":"m",
            "a":{
                "chatUuid":chatUuid,
                "name":name,
                "content":res
            }
        }
        try:
            self.send(message)
            res = self.findRes()
            if res["m"] == "R":
                return res["r"]
        except Exception as e:
            logger.error(f"{e}")
        return None

class Peer:
    def __init__(self):
        pass
    def listenForMessages(self):
        port = settings["port"]
        logger.debug(f"Start Listen - {port}")
        while True:
            try:
                content, addr = sock.recvfrom(1048576)
                content = json.loads(base64.b64decode(content))
                if content.get("m") == "R":
                    with resesLock, requestNumLock:
                        if requestNum < len(reses)+1 or not content.get("z"):
                            continue
                        reses[content["z"]] = content
                        continue
                threading.Thread(target=self.handleMessage, args=(content, addr), daemon=True).start()
            except:
                pass
    def handleMessage(self, message:dict, addr):
        server = Server(message, message.get("z"), addr)
        with onceConnectedIpsLock:
            onceConnectedIps.append(addr[0])
        try:
            if message["m"] == "p":
                message = {
                    "m":"R",
                    "r":0,
                    "c":{}
                }
                server.send(message)
                return
            if message["m"] == "r":
                if message["t"] == "g":
                    if not settings["amIBootstrapPeer"]:
                        if message["d"] == "c":
                            server.reqChatList(message["a"]["maxChatsLength"])
                        if message["d"] == "m":
                            server.reqMessages(message["a"]["chatUuid"])
                    if message["d"] == "i":
                        server.reqPeersList(message["a"]["maxPeersLength"])
                elif message["t"] == "r":
                    if message["d"] == "i":
                        server.registerPeer(message["a"])
                elif message["t"] == "s":
                    if not settings["amIBootstrapPeer"]:
                        if message["d"] == "m":
                            server.sendMessage(message["a"])
            message = {
                "m":"R",
                "r":2,
                "c":{}
            }
            server.send(message)
        except Exception as e:
            logger.error(f"Exception in handleMessage:{e}")
            message = {
                "m":"R",
                "r":1,
                "c":{}
            }
            server.send(message)
    def syncer(self):
        global custodyChats
        count = 0
        while True:
            tempCustodyChats:dict[list[dict]] = {}
            if count % 4:
                for server in settings["bootstrapPeers"]:
                    client = Client(server["ip"], server["port"])
                    client.getPeers()
            cursor.execute("SELECT * FROM peers")
            peers = cursor.fetchall()
            for peer in random.sample(peers, min(len(peers), 30)):
                client = Client(peer[1], peer[2])
                if not client.ping():
                    logger.warning(f"Dead Peer:{peer[1]}")
                    cursor.execute("DELETE FROM peers WHERE ip=? AND port=?", (peer[1], peer[2]))
                    conn.commit()
                    continue
                if not settings["amIBootstrapPeer"]:
                    client.getPeers()
                    tempCustodyChats[f"{peer[1]}:{peer[2]}"] = client.getChats()
                    client.registerPeer()
            with custodyChatsLock:
                custodyChats = tempCustodyChats
            count += 1
            time.sleep(60)
    def start(self):
        threading.Thread(target=self.listenForMessages).start()
        time.sleep(2)
        if not settings["amIBootstrapPeer"]:
            for server in settings["bootstrapPeers"]:
                client = Client(server["ip"], server["port"])
                if not client.ping():
                    logger.warning(f"Dead BootstrapPeer:{server}")
                    continue
                while True:
                    if client.registerPeer():
                        break
                client.getPeers()
        self.syncer()

class Web:
    app = Flask(import_name="NanceChat", template_folder="src/templates")
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    cursor = None
    def __init__(self, host="0.0.0.0", port=8080):
        self.host = host
        self.port = port
    @app.route("/")
    def index():
        return render_template("index.html")
    @app.route("/peerList")
    def peerList():
        cursor.execute("SELECT * FROM peers")
        peers = cursor.fetchall()
        return render_template("peerList.html", peers=peers)
    @app.route("/chatList")
    def chatList():
        with custodyChatsLock:
            chatListA = copy.deepcopy(custodyChats)
        cursor.execute("SELECT * FROM chats")
        localChats:list[dict] = []
        for chat in cursor.fetchall():
            localChats.append({"uuid": chat[0], "name": chat[1], "timestamp": chat[2]})
        chatListA["this"] = localChats
        for i in chatListA.keys():
            if not chatListA[i]:
                continue
            for j in range(len(chatListA[i])):
                chatListA[i][j]["timestamp"] = str(datetime.datetime.fromtimestamp(float(chatListA[i][j]["timestamp"])))
        return render_template("chatList.html", chatList=chatListA)
    @app.route("/newChat", methods=["POST", "GET"])
    def newChat():
        if request.method == "POST":
            chatUuid = str(uuid.uuid4())
            name = request.form.get("name")
            firstMessage = request.form.get("firstMessage")
            if not (name and firstMessage):
                return render_template("newChat.html")
            cursor.execute("INSERT INTO chats (uuid, name, timestamp) VALUES (?, ?, ?)", (chatUuid, str(name), datetime.datetime.timestamp(datetime.datetime.now())))
            conn.commit()
            cursor.execute("INSERT INTO messages (uuid, chatUuid, name, content, timestamp) VALUES (?, ?, ?, ?, ?)", (str(uuid.uuid4()), chatUuid, "first", firstMessage, datetime.datetime.timestamp(datetime.datetime.now())))
            conn.commit()
            return redirect(f"chat?id={chatUuid}")
        else:
            return render_template("newChat.html")
    @app.route("/chat", methods=["POST", "GET"])
    def chat():
        try:
            if request.method == "POST":
                chatUuid = request.form.get("chatUuid")
                server = request.form.get("server")
                name = request.form.get("name")
                content = request.form.get("content")
                if not (chatUuid and server and content and name):
                    return redirect("chatList")
                if server == "this":
                    cursor.execute("SELECT * FROM messages WHERE chatUuid=?", (chatUuid,))
                    messages = cursor.fetchall()
                    if not messages:
                        return redirect(url_for("chatList", _method="GET"))
                    messages:list[dict] = [{"uuid":message[0], "name":message[2], "content":message[3], "timestamp":message[4]} for message in messages]
                    messageUuid = str(uuid.uuid4())
                    timestamp = str(datetime.datetime.timestamp(datetime.datetime.now()))
                    messages.append({"messageUuid":messageUuid, "chatUuid":chatUuid, "name":name, "content":content, "timestamp":timestamp})
                    if not len(base64.b64encode(json.dumps({"m":"R","r":0,"c":{"messages":messages}}).encode("utf-8"))) < settings["messagesMaxSize"]:
                        return redirect(url_for("chat", _method="GET", id=chatUuid, status="1"))
                    cursor.execute("INSERT INTO messages (uuid, chatUuid, name, content, timestamp) VALUES (?, ?, ?, ?, ?)", (str(uuid.uuid4()), chatUuid, name, content, datetime.datetime.timestamp(datetime.datetime.now())))
                    conn.commit()
                    return redirect(url_for("chat", _method="GET", id=chatUuid, status="0"))
                else:
                    cursor.execute("SELECT * FROM peers WHERE ip=? AND port=?", (server.split(":")[0], int(server.split(":")[1])))
                    peer = cursor.fetchall()
                    client = Client(peer[0][1], peer[0][2])
                    result = client.sendMessage(chatUuid, name, content)
                    time.sleep(2)
                    if result == 0:
                        return redirect(url_for("chat", _method="GET", id=chatUuid, server=server, status="0"))
                    else:
                        return redirect(url_for("chat", _method="GET", id=chatUuid, server=server, status="1"))
            else:
                messages:list[dict] = []
                chatUuid = request.args.get("id")
                if not (chatUuid):
                    return redirect(url_for("chatList"))
                server = request.args.get("server")
                status = request.args.get("status", "-1")
                status = int(status)
                err = ""
                if status == 1:
                    err = "チャットのサイズの制限でこれ以上は投稿できません。"
                elif status == 0:
                    err = "yaso"
                if server:
                    cursor.execute("SELECT * FROM peers WHERE ip=? AND port=?", (server.split(":")[0], int(server.split(":")[1])))
                    peer = cursor.fetchall()
                    client = Client(peer[0][1], peer[0][2])
                    messages = client.getMessages(chatUuid)
                    if not messages:
                        return redirect(url_for("chatList"))
                else:
                    server = "this"
                    cursor.execute("SELECT * FROM messages WHERE chatUuid=?", (chatUuid,))
                    messages = cursor.fetchall()
                    messages = [{"uuid":message[0], "name":message[2], "content":message[3], "timestamp":message[4]} for message in messages]
                for i in range(len(messages)):
                    if not messages[i]:
                        continue
                    messages[i]["timestamp"] = str(datetime.datetime.fromtimestamp(float(messages[i]["timestamp"])))
                messages.reverse()
                return render_template("chat.html", messages=messages, chatUuid=chatUuid, server=server, err=err)
        except:
            logger.error(traceback.format_exc())
            return render_template("error.html")
    def runWeb(self):
        threading.Thread(target=Utils.openBrowser, args=(f"http://127.0.0.1:{self.port}",)).start()
        self.app.run(self.host, self.port)

def start():
    global settings
    peer = Peer()
    web = Web()
    if not settings["amIBootstrapPeer"]:
        threading.Thread(target=peer.start, daemon=True).start()
        web.runWeb()
    else:
        peer.start()

def scheduleDeletion():
    while True:
        time.sleep(24*60*60)
        Utils.allDeleteMessagesAndChats()

if __name__ == "__main__":
    while True:
        port = random.randint(50000, 54000)
        if Utils.isPortAvailable(port):
            settings = Settings(port=port).settings
        else:
            continue
        break
    time.sleep(2)
    if not settings["amIBootstrapPeer"]:
        while True:
            randomStunServer = random.choice(settings["stunServers"])
            natConeType, ip, port = Utils.getStun(randomStunServer["ip"], randomStunServer["port"])
            info["natConeType"] = natConeType
            if not (natConeType and ip and port):
                continue
            break
    port = settings["port"]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("", port))
    try:
        threading.Thread(target=scheduleDeletion, daemon=True).start()
        start()
    except:
        logger.error("huh? E R R O R\n"+traceback.format_exc())
    finally:
        cursor.close()
        conn.close()

# junking codeee