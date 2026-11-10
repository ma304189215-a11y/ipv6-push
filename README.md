# IP6 Push —— 公网 IPv6 地址变化推送

<!-- series-nav -->
> **自建服务器小工具集** · [nopass](https://github.com/ma304189215-a11y/nopass)（内网服务免密） · **ipv6-push**（IPv6 变化推送） · [wifipay](https://github.com/ma304189215-a11y/wifipay)（付费 WiFi 认证）
>
> 都在自己家里跑了一段时间、确实在用的东西。零第三方依赖，装完就能用。
<!-- series-nav -->

给家里/公司服务器用的小工具：**本机公网 IPv6 变了就推一条消息到手机**。

带一个网页管理界面，改配置不用动文件、不用重启容器。

---

## 拉取

```sh
git clone https://github.com/ma304189215-a11y/ipv6-push.git
cd ipv6-push
```

或者只取发布包（不需要 git）：

```sh
wget https://github.com/ma304189215-a11y/ipv6-push/archive/refs/heads/main.tar.gz
tar xzf main.tar.gz && cd ipv6-push-main
```

---

## 为什么需要它

家宽 IPv6 前缀会随拨号变化（我这里主路由每天凌晨 3 点重拨，`/64` 整段换掉）。
每次换完，用 IPv6 直连回家就断了 —— 因为记在手机里的地址已经过期。

这个容器做的事很简单：**每 N 秒查一次自己的公网 IPv6，变了就推送新地址**。

---

## 快速开始

### 1. 起容器

```sh
export ADMIN_PASS='设一个你自己的密码'
docker compose up -d --build
```

> `ADMIN_PASS` 只在**首次启动**用来初始化管理员。之后改密码在网页里改，
> 存在 `data/config.json`（存的是 sha256，不是明文）。
> 不设 `ADMIN_PASS` 的话首次会被初始化成空密码 —— **别这么干**。

### 2. 打开管理页

```
http://<服务器IP>:8099
```

用 `admin` + 你刚设的密码登录。

### 3. 填推送通道

页面里三选一：

| 通道 | 需要的值 | 怎么拿 |
|---|---|---|
| **企业微信群机器人**（推荐） | `wecom_key` | 群聊 → 右上角 → 群机器人 → 添加 → 复制 Webhook 里 `key=` 后面那段 |
| **Server酱** | `serverchan_key` | https://sct.ftqq.com 登录后拿 SendKey |
| **自定义 Webhook** | `custom_url` | 你的服务器，POST JSON 过去 |

点「测试推送」确认能收到，再勾上「启用推送」。

---

## 配置项

管理页里都能改，也可以直接编辑 `data/config.json`。

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 推送开关 |
| `channel` | `wecom` | `wecom` / `serverchan` / `custom` |
| `wecom_key` | 空 | 企业微信机器人 key |
| `serverchan_key` | 空 | Server酱 SendKey |
| `custom_url` | 空 | 自定义 Webhook |
| `msg_prefix` | 空 | 消息前缀，多台机器时用来区分（如 `98`） |
| `device` | 空 | 指定网卡（如 `eth0`），留空自动探测 |
| `interval` | `600` | 检测周期（秒），最小 60 |
| `period` | `172800` | 定时重推间隔（秒），默认 2 天 |
| `source` | `both` | `local` / `remote` / `both` |
| `remote_urls` | 6 个公开回显站 | 远端 IPv6 回显地址列表 |

### 关于 `source`

- `local`：只读本机网卡上的全局 IPv6
- `remote`：只问外部回显站「你看我是谁」
- `both`（推荐）：两边都取，**以本机网卡为准**，远端只做交叉验证

`both` 的好处：如果本机网卡读到全局地址，用它（最准）；读不到再退回远端回显。
远端站有 6 个，按顺序试，任一成功即止。

### 关于 `period`

即使地址没变，每 2 天也会重推一次。用途：手机上那条旧消息被刷掉后，还能翻到最新地址。

---

## 端口与网络

- 监听 `8099`（环境变量 `PORT` 可改）
- **必须 `network_mode: host`** —— 要读宿主机网卡的真实 IPv6，bridge 网络里读不到

---

## 常见问题

**页面显示「当前本机 IPv6：未取到」**

容器不是 host 网络，或者宿主机上确实没有全局 IPv6。检查：

```sh
ip -6 addr show scope global
```

**测试推送失败**

看页面底部的日志区，或者：

```sh
docker logs wxpush --tail 50
```

企业微信常见错：`93000` = key 不对；`45009` = 触发频率限制（20 条/分钟）。

**地址变了但没推**

`interval` 是最小检测间隔。设 600 就是最多延迟 10 分钟。

**忘记密码**

```sh
# 停容器，删掉 config.json 里的 admin_pass_sha256，重新用 ADMIN_PASS 起
docker compose down
sed -i 's/"admin_pass_sha256": "[^"]*"/"admin_pass_sha256": ""/' data/config.json
ADMIN_PASS='新密码' docker compose up -d
```

---

## 依赖

- Docker + compose
- 镜像内：`python:3.11-slim` + `curl` + `iproute2`
- **代码零第三方 Python 包**，只用标准库

---

## 文件

```
app.py                主程序（HTTP 服务 + 定时任务 + 管理页，单文件）
Dockerfile
docker-compose.yml
data/                 运行时数据（已 gitignore）
  config.json         配置
  state.json          上次推送的地址与时间
  wxpush.log          日志
```
