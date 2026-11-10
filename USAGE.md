# IP6 Push 使用手册

## 一、它是干什么的

家宽 IPv6 前缀会变。一变，你手机里存的那个地址就失效了，用 IPv6 直连回家直接断。

这个容器每隔一段时间查一次自己的公网 IPv6，**变了就推一条消息给你**（企业微信 / Server酱 / 自定义 Webhook）。消息里带新地址，复制就能用。

带网页管理界面，改配置不用动文件。

---

## 二、拉取并启动

### 方式 1：git clone（推荐，便于更新）

```sh
git clone https://github.com/ma304189215-a11y/ipv6-push.git
cd ipv6-push
export ADMIN_PASS='改成你自己的密码'
docker compose up -d --build
```

### 方式 2：直接下载压缩包

```sh
wget https://github.com/ma304189215-a11y/ipv6-push/archive/refs/heads/main.tar.gz
tar xzf main.tar.gz
cd ipv6-push-main
export ADMIN_PASS='改成你自己的密码'
docker compose up -d --build
```

### 方式 3：不用 Docker

```sh
mkdir -p /opt/ipv6-push/data
cp app.py /opt/ipv6-push/
cd /opt/ipv6-push
PORT=8099 ADMIN_USER=admin ADMIN_PASS='你的密码' DATA_DIR=./data nohup python3 -u app.py > data/run.log 2>&1 &
```

> 需要宿主机有 `curl` 和 `ip`（`iproute2`）。

---

## 三、首次配置

打开 `http://<服务器IP>:8099`，用 `admin` + `ADMIN_PASS` 登录。

### 1. 选推送通道

| 通道 | 值 | 怎么拿 |
|---|---|---|
| **企业微信群机器人** | `wecom_key` | 企业微信群 → 右上角 → 群机器人 → 添加 → 复制 Webhook URL 里 `key=` 后面那段（形如 `693axxx-xxxx`） |
| **Server酱** | `serverchan_key` | https://sct.ftqq.com 微信登录 → 复制 SendKey（形如 `SCTxxxxxx`） |
| **自定义 Webhook** | `custom_url` | 你自己的服务，POST JSON |

### 2. 填消息前缀（可选但建议）

`msg_prefix` 比如填 `98`，收到消息就是：

```
[98 IPv6_CHANGED] myhost IPv6: 240e:351:9894:f400:be24:11ff:fec4:83bc
2026-11-11 03:55:47
```

多台机器时靠这个区分。

### 3. 测试

点「测试推送」，手机收到就说明通了。然后勾上「启用推送」。

---

## 四、参数详解

### 检测周期 `interval`

默认 `600` 秒（10 分钟），最小 60。

- 太短：白白调外部接口
- 太长：换地址后最多等这么久才知道

**建议**：知道自己的拨号时间的话，在拨号后那段时间把 interval 调小（比如 120），平时 600。

### 定时重推 `period`

默认 `172800` 秒（2 天）。即使地址没变也重推一次。

用途：手机上那条旧消息被别的消息刷没了，还能翻到最新的地址。

### 地址来源 `source`

| 值 | 行为 |
|---|---|
| `local` | 只读本机网卡的全局 IPv6（最准，但容器必须是 host 网络） |
| `remote` | 只问外部回显站「你看到我的地址是什么」 |
| `both` | 两边都取，**本机优先**，远端交叉验证（推荐） |

远端回显默认 6 个，按顺序试，任一成功即止：

```
https://api6.ipify.org/
https://ipv6.icanhazip.com/
https://v6.ident.me/
https://ifconfig.co/ip
https://ipv6.ip.sb/ip
http://v6.010504.xyz:8989/
```

国内网络访问境外站点容易超时，可以在页面里**删掉几个**只留国内的，响应更快。

### 网卡 `device`

留空自动探测。多网卡、或者有 docker 网桥干扰时，明确指定（如 `eth0`）。

---

## 五、文件与数据

```
data/
  config.json   配置（含各通道 key、管理员密码 sha256）
  state.json    上次推送的地址和时间戳
  wxpush.log    运行日志（页面底部也显示最后 40 行）
```

备份就拷 `data/` 整个目录。迁移到新机器时把 `data/` 带过去即可，配置全在里头。

---

## 六、排查

### 「当前本机 IPv6：未取到」

1. 容器不是 host 网络 → 改 `docker-compose.yml` 的 `network_mode: host`
2. 宿主机真没全局 IPv6 → `ip -6 addr show scope global`

### 推送失败

看日志：

```sh
docker logs wxpush --tail 50
```

企业微信错误码：

| errcode | 含义 |
|---|---|
| `93000` | key 不对 |
| `45009` | 触发频率限制（20 条/分钟） |
| `40008` | 消息格式错 |

### 地址变了没推

- `interval` 设太大
- 只在地址**变化**时推，`period` 到期才无条件重推

### 忘记密码

```sh
docker compose down
sed -i 's/"admin_pass_sha256": "[^"]*"/"admin_pass_sha256": ""/' data/config.json
ADMIN_PASS='新密码' docker compose up -d
```

---

## 七、安全

- `ADMIN_PASS` 只在首次启动初始化，之后以 `data/config.json` 里的 sha256 为准
- **不设 `ADMIN_PASS` 会被初始化成空密码**——务必设
- 管理页只在局域网用。要外网访问请套反代加 HTTPS
- 各通道的 key 明文存在 `config.json`，权限自己收紧（`chmod 600 data/config.json`）
