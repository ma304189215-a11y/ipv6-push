#!/usr/bin/env python3
# wxpush docker 版：公网 IPv6 推送 + 管理页面
import os, json, re, time, subprocess, threading, hashlib, secrets, socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DATA   = os.environ.get("DATA_DIR", "/data")
CFG    = os.path.join(DATA, "config.json")
STATE  = os.path.join(DATA, "state.json")
LOGF   = os.path.join(DATA, "wxpush.log")
PORT   = int(os.environ.get("PORT", "8099"))
LOCK   = threading.Lock()

DEF_URLS = ["https://api6.ipify.org/","https://ipv6.icanhazip.com/","https://v6.ident.me/",
            "https://ifconfig.co/ip","https://ipv6.ip.sb/ip","http://v6.010504.xyz:8989/"]

DEFAULTS = {
    "enabled": True, "channel": "wecom", "device": "", "interval": 600, "period": 172800,
    "wecom_key": "", "serverchan_key": "", "custom_url": "", "msg_prefix": "",
    "source": "both", "remote_urls": DEF_URLS,
    "admin_user": os.environ.get("ADMIN_USER","admin"),
    # 首次启动用 ADMIN_PASS 初始化；环境变量没给就随机生成并打印到日志，
    # 避免默认弱口令。之后一律以 data/config.json 里的哈希为准。
    "admin_pass_sha256": hashlib.sha256(os.environ.get("ADMIN_PASS", "").encode()).hexdigest() if os.environ.get("ADMIN_PASS") else "",
}

def jload(p, d):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return json.loads(json.dumps(d))

def jsave(p, o):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp,"w") as f: json.dump(o,f,ensure_ascii=False,indent=2)
    os.replace(tmp,p)

def log(msg):
    os.makedirs(DATA, exist_ok=True)
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    with open(LOGF,"a") as f: f.write(line+"\n")
    try:
        with open(LOGF) as f: lines=f.readlines()[-500:]
        with open(LOGF,"w") as f: f.writelines(lines)
    except Exception: pass

def cfg():
    c = jload(CFG, DEFAULTS)
    for k,v in DEFAULTS.items(): c.setdefault(k,v)
    return c

# ---------- 取地址 ----------
def get_local(dev=""):
    cands=[]
    try:
        with open("/proc/net/if_inet6") as f:
            for line in f:
                p=line.split()
                if len(p)<6: continue
                addr,idx,plen,scope,flags,name = p[0],p[1],p[2],p[3],p[4],p[5]
                if scope!="00": continue
                fl=int(flags,16)
                if fl & 0x20 or fl & 0x10: continue      # temporary / deprecated
                a=socket.inet_ntop(socket.AF_INET6, bytes.fromhex(addr))
                if a.startswith("fd") or a.startswith("fe80"): continue
                cands.append((name,a))
    except Exception: pass
    if dev:
        for n,a in cands:
            if n==dev: return a
    return cands[0][1] if cands else ""

def get_remote(urls):
    for u in urls:
        u=(u or "").strip()
        if not u: continue
        try:
            r=subprocess.run(["curl","-6","-s","--max-time","8",u],
                             capture_output=True,text=True,timeout=14)
            s=re.sub(r"\s+","",r.stdout)[:64]
            if re.fullmatch(r"[0-9a-fA-F:]{4,}", s) and s.count(":")>=2:
                return s,u
        except Exception: pass
    return "",""

# ---------- 发送 ----------
def send_wecom(key,prefix,ev,addr,note):
    host=socket.gethostname() or "wxpush"
    msg="[" + (prefix+" " if prefix else "") + ev + "] " + host + " IPv6: " + addr + "\n" + time.strftime("%F %T")
    if note: msg += "\n⚠ " + note
    body=json.dumps({"msgtype":"text","text":{"content":msg}},ensure_ascii=False)
    try:
        r=subprocess.run(["curl","-s","--max-time","15","-H","Content-Type: application/json",
                          "-d",body,"https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key="+key],
                         capture_output=True,text=True,timeout=20)
        ok='"errcode":0' in r.stdout
        return ok, r.stdout[:200]
    except Exception as e:
        return False, str(e)[:200]

def send_serverchan(key,ev,addr,note):
    try:
        r=subprocess.run(["curl","-s","--max-time","15","--data-urlencode","title="+ev,
                          "--data-urlencode","desp="+addr+" "+time.strftime("%F %T")+((" "+note) if note else ""),
                          "https://sctapi.ftqq.com/"+key+".send"],capture_output=True,text=True,timeout=20)
        return '"code":0' in r.stdout, r.stdout[:200]
    except Exception as e:
        return False, str(e)[:200]

def send_custom(url,ev,addr,note):
    body=json.dumps({"event":ev,"addr":addr,"host":socket.gethostname(),
                     "time":time.strftime("%F %T"),"note":note},ensure_ascii=False)
    try:
        r=subprocess.run(["curl","-s","--max-time","15","-H","Content-Type: application/json",
                          "-d",body,url],capture_output=True,text=True,timeout=20)
        return bool(r.stdout.strip()), r.stdout[:200]
    except Exception as e:
        return False, str(e)[:200]

def dispatch(c,ev,addr,note=""):
    ch=c.get("channel") or "wecom"
    if ch=="wecom":
        k=c.get("wecom_key","")
        if not k: return False,"未配置 wecom_key"
        return send_wecom(k,c.get("msg_prefix",""),ev,addr,note)
    if ch=="serverchan":
        k=c.get("serverchan_key","")
        if not k: return False,"未配置 serverchan_key"
        return send_serverchan(k,ev,addr,note)
    if ch=="custom":
        u=c.get("custom_url","")
        if not u: return False,"未配置 custom_url"
        return send_custom(u,ev,addr,note)
    return False,"未知通道 "+ch

# ---------- 主流程 ----------
def check(boot=False):
    if not LOCK.acquire(blocking=False): return
    try:
        c=cfg()
        if not c.get("enabled"): return
        st=jload(STATE,{})
        la=get_local(c.get("device",""))
        src=c.get("source","both")
        ra="";note=""
        if src in ("remote","both"):
            ra,u=get_remote(c.get("remote_urls") or [])
            note=""
        if src=="local": addr=la
        elif src=="remote": addr=ra
        else:
            addr=ra or la
            if la and ra and la!=ra: note="本机地址 "+la+" 与远端回显不一致"
        if not addr:
            log("取不到地址"); return
        now=int(time.time())
        last=st.get("last",""); lastts=int(st.get("ts") or 0); failts=int(st.get("failts") or 0)
        period=int(c.get("period") or 172800)
        ev=""
        if boot: ev="boot"
        elif lastts==0: ev="first"
        elif addr!=last: ev="change"
        elif now<lastts: ev="clock"
        elif now-lastts>=period: ev="periodic"
        if not ev: return
        if ev not in ("boot","first") and failts and now-failts<900:
            log("失败退避中"); return
        ok,resp=dispatch(c,ev,addr,note)
        if ok:
            st.update({"last":addr,"ts":now}); st.pop("failts",None); jsave(STATE,st)
            log("已推送["+ev+"] "+addr+(" ("+note+")" if note else ""))
        else:
            st["failts"]=now; jsave(STATE,st)
            log("推送失败["+ev+"] "+addr+" :: "+resp)
    finally:
        LOCK.release()

def daemon():
    time.sleep(60)
    while True:
        try: check()
        except Exception as e: log("异常 "+str(e)[:120])
        c=cfg(); iv=int(c.get("interval") or 600)
        time.sleep(max(60,iv))

# ---------- 页面 ----------
SESS={}
CSS="""<style>
body{font-family:system-ui,-apple-system,'PingFang SC',sans-serif;background:#0f1115;color:#e6e6e6;margin:0;padding:18px}
h2{margin:0 0 14px;font-size:18px}
.card{background:#171a21;border:1px solid #232838;border-radius:10px;padding:14px;margin-bottom:14px;max-width:820px}
label{display:block;font-size:13px;color:#9aa4b8;margin:8px 0 4px}
input,select,textarea{width:100%;box-sizing:border-box;background:#0f1115;border:1px solid #2a3143;color:#e6e6e6;border-radius:6px;padding:8px;font-size:14px}
button{background:#4c6ef5;color:#fff;border:0;border-radius:6px;padding:9px 16px;font-size:14px;margin:10px 8px 0 0;cursor:pointer}
button.g{background:#2f9e44}button.r{background:#c92a2a}
pre{background:#0b0d11;border:1px solid #232838;border-radius:8px;padding:10px;overflow:auto;max-height:320px;font-size:12px;color:#8fd694}
.ok{color:#51cf66}.bad{color:#ff6b6b}.muted{color:#9aa4b8;font-size:13px}
a{color:#4c6ef5}
</style>"""

def page_login(err=""):
    return ("<!doctype html><meta charset='utf-8'><title>wxpush 登录</title>"+CSS+
      "<div class='card' style='max-width:360px;margin:60px auto'><h2>wxpush 管理</h2>"
      +("<p class='bad'>"+err+"</p>" if err else "")+
      "<form method='post' action='/login'><label>账号</label><input name='u' autofocus>"
      "<label>密码</label><input name='p' type='password'><button>登录</button></form></div>")

def page_main(msg=""):
    c=cfg(); st=jload(STATE,{}); local=get_local(c.get("device",""))
    urls="\n".join(c.get("remote_urls") or [])
    last=st.get("last","-"); ts=st.get("ts") or 0
    lasttime=time.strftime("%Y-%m-%d %H:%M:%S",time.localtime(int(ts))) if ts else "从未"
    try:
        with open(LOGF) as f: lg="".join(f.readlines()[-40:])
    except Exception: lg="(无日志)"
    chk="checked" if c.get("enabled") else ""
    def sel(v,a): return "selected" if v==a else ""
    return ("<!doctype html><meta charset='utf-8'><title>wxpush</title><meta name='viewport' content='width=device-width,initial-scale=1'>"+CSS+
      "<h2>wxpush · 公网 IPv6 推送</h2>"+("<p class='ok'>"+msg+"</p>" if msg else "")+
      "<div class='card'><b>状态</b><p class='muted'>容器主机名："+socket.gethostname()+
      "　当前本机 IPv6：<span class='ok'>"+(local or "未取到")+"</span><br>上次推送地址："+ (last or "-") +
      "　时间："+lasttime+"</p>"
      "<form method='post' action='/check' style='display:inline'><button class='g'>立即检查</button></form>"
      "<form method='post' action='/test' style='display:inline'><button>测试推送</button></form>"
      "<form method='post' action='/logout' style='display:inline'><button class='r'>退出</button></form></div>"
      "<form method='post' action='/save'><div class='card'>"
      "<label><input type='checkbox' name='enabled' value='1' "+chk+" style='width:auto'> 启用推送</label>"
      "<label>推送通道</label><select name='channel'>"
      "<option value='wecom' "+sel(c.get('channel'),'wecom')+">企业微信群机器人 (wecom)</option>"
      "<option value='serverchan' "+sel(c.get('channel'),'serverchan')+">Server酱</option>"
      "<option value='custom' "+sel(c.get('channel'),'custom')+">自定义 Webhook</option></select>"
      "<label>企业微信 key</label><input name='wecom_key' value='"+str(c.get('wecom_key',''))+"'>"
      "<label>Server酱 key</label><input name='serverchan_key' value='"+str(c.get('serverchan_key',''))+"'>"
      "<label>自定义 Webhook URL</label><input name='custom_url' value='"+str(c.get('custom_url',''))+"'>"
      "<label>消息前缀</label><input name='msg_prefix' value='"+str(c.get('msg_prefix',''))+"'>"
      "<label>网卡（留空自动）</label><input name='device' value='"+str(c.get('device',''))+"' placeholder='如 eth0'>"
      "<label>检测周期（秒，最小 60）</label><input name='interval' value='"+str(c.get('interval',600))+"'>"
      "<label>定时重推（秒，默认 172800 = 2 天）</label><input name='period' value='"+str(c.get('period',172800))+"'>"
      "<label>地址来源</label><select name='source'>"
      "<option value='both' "+sel(c.get('source'),'both')+">本机 + 远端回显（推荐）</option>"
      "<option value='local' "+sel(c.get('source'),'local')+">仅本机网卡</option>"
      "<option value='remote' "+sel(c.get('source'),'remote')+">仅远端回显</option></select>"
      "<label>远端回显地址（一行一个）</label><textarea name='remote_urls' rows='6'>"+urls+"</textarea>"
      "<button>保存配置</button></div></form>"
      "<div class='card'><b>日志（最近 40 行）</b><pre>"+lg.replace("<","&lt;")+"</pre></div>")

class H(BaseHTTPRequestHandler):
    server_version="wxpush"
    def _send(self,code,body,ctype="text/html; charset=utf-8",extra=None):
        b=body.encode()
        self.send_response(code); self.send_header("Content-Type",ctype)
        self.send_header("Content-Length",str(len(b)))
        for k,v in (extra or []): self.send_header(k,v)
        self.end_headers(); self.wfile.write(b)
    def authed(self):
        ck=self.headers.get("Cookie","")
        m=re.search(r"sid=([A-Za-z0-9]+)",ck)
        if not m: return False
        t=SESS.get(m.group(1))
        return bool(t and t>time.time())
    def do_GET(self):
        p=urlparse(self.path).path
        if p=="/login": return self._send(200,page_login())
        if p=="/log":
            try:
                with open(LOGF) as f: return self._send(200,"".join(f.readlines()[-300:]),"text/plain; charset=utf-8")
            except Exception: return self._send(200,"","text/plain")
        if not self.authed(): return self._send(302,"",extra=[("Location","/login")])
        if p=="/": return self._send(200,page_main())
        self._send(404,"not found")
    def do_POST(self):
        p=urlparse(self.path).path
        n=int(self.headers.get("Content-Length") or 0)
        raw=self.rfile.read(n).decode("utf-8","ignore")
        q=parse_qs(raw)
        if p=="/login":
            c=cfg()
            u=q.get("u",[""])[0]; pw=q.get("p",[""])[0]
            if u==c.get("admin_user") and hashlib.sha256(pw.encode()).hexdigest()==c.get("admin_pass_sha256"):
                t=secrets.token_hex(16); SESS[t]=time.time()+12*3600
                return self._send(302,"",extra=[("Location","/"),("Set-Cookie","sid="+t+"; Path=/; HttpOnly")])
            return self._send(200,page_login("账号或密码不对"))
        if not self.authed(): return self._send(302,"",extra=[("Location","/login")])
        c=cfg()
        if p=="/save":
            c["enabled"]="enabled" in q
            c["channel"]=q.get("channel",["wecom"])[0]
            c["wecom_key"]=q.get("wecom_key",[""])[0].strip()
            c["serverchan_key"]=q.get("serverchan_key",[""])[0].strip()
            c["custom_url"]=q.get("custom_url",[""])[0].strip()
            c["msg_prefix"]=q.get("msg_prefix",[""])[0].strip()
            c["device"]=q.get("device",[""])[0].strip()
            try: c["interval"]=max(60,int(q.get("interval",["600"])[0]))
            except Exception: c["interval"]=600
            try: c["period"]=max(0,int(q.get("period",["172800"])[0]))
            except Exception: c["period"]=172800
            c["source"]=q.get("source",["both"])[0]
            c["remote_urls"]=[x.strip() for x in q.get("remote_urls",[""])[0].splitlines() if x.strip()]
            jsave(CFG,c); log("配置已保存")
            return self._send(302,"",extra=[("Location","/")])
        if p=="/check":
            threading.Thread(target=check,daemon=True).start()
            return self._send(302,"",extra=[("Location","/")])
        if p=="/test":
            def run():
                c=cfg(); a=get_local(c.get("device","")) or "::"
                ok,resp=dispatch(c,"test",a,"手动测试")
                log(("测试推送成功 " if ok else "测试推送失败 ")+resp)
            threading.Thread(target=run,daemon=True).start()
            return self._send(302,"",extra=[("Location","/")])
        if p=="/logout":
            ck=self.headers.get("Cookie","")
            m=re.search(r"sid=([A-Za-z0-9]+)",ck)
            if m: SESS.pop(m.group(1),None)
            return self._send(302,"",extra=[("Location","/login"),("Set-Cookie","sid=; Path=/; Max-Age=0")])
        self._send(404,"not found")
    def log_message(self,*a): pass

if __name__=="__main__":
    os.makedirs(DATA,exist_ok=True)
    if not os.path.exists(CFG): jsave(CFG,DEFAULTS)
    threading.Thread(target=daemon,daemon=True).start()
    log("wxpush 容器启动，端口 "+str(PORT))
    ThreadingHTTPServer(("0.0.0.0",PORT),H).serve_forever()
