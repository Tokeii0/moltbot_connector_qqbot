# Moltbot Connector

NoneBot2 插件，用于连接 Moltbot Gateway，实现 QQ 与 Moltbot AI 助手的桥接。

## 安装依赖

```bash
pip install websockets
```

## 前置准备：配置 NapCat HTTP SSE 服务端

在使用本插件之前，需要先完成以下步骤：

### 步骤 1: 确保 NoneBot 和 NapCat 能够通信

首先确保你的 NoneBot2 已经能够正常连接到 NapCat，可以正常收发 QQ 消息。

### 步骤 2: 在 NapCat 中配置 HTTP SSE 服务端

1. 打开 NapCat 的配置文件
2. 找到 HTTP SSE 服务端配置项
3. 启用 HTTP SSE 服务端（使用默认配置即可）
4. **重要**: 记下服务端的 **token 密码**（如果有设置的话）

### 步骤 3: 将 token 密码告知 Moltbot

使用以下命令让 Moltbot 记住 NapCat 的 token 密码：

```bash
# 在 Moltbot 中执行
moltbot config set napcat_token "你的token密码"
```

### 步骤 4: 配置本插件

完成上述步骤后，继续配置本插件的其他内容（见下方配置说明）。

## 配置

在 `.env` 或 `.env.prod` 文件中添加以下配置：

```env
# Moltbot Gateway WebSocket 地址（默认: ws://127.0.0.1:18789）
MOLTBOT_GATEWAY_URL=ws://127.0.0.1:18789

# 认证 token（可选，如果 Gateway 配置了认证）
MOLTBOT_TOKEN=

# 认证密码（可选）
MOLTBOT_PASSWORD=

# 请求超时时间（秒，默认: 60）
MOLTBOT_TIMEOUT=60

# 调试模式
MOLTBOT_DEBUG=false

# 允许使用的 QQ 号白名单（用逗号分隔，留空表示允许所有人）
MOLTBOT_ALLOWED_USERS=664275355
```

**白名单配置说明**:
- 如果设置了 `MOLTBOT_ALLOWED_USERS`，只有列表中的 QQ 号可以与 Moltbot 交互
- 多个 QQ 号用逗号分隔，例如: `MOLTBOT_ALLOWED_USERS=664275355,123456789`
- 留空或不设置该项表示允许所有人使用

## 使用方法

1. 确保 Moltbot Gateway 正在运行：
   ```bash
   moltbot gateway --port 18789
   ```

2. 启动 NoneBot2 机器人

3. 在 QQ 中 @机器人 发送消息，机器人会将消息转发给 Moltbot 并返回响应

## 工作原理

```
QQ 用户 → NoneBot2 → Moltbot Gateway → AI Agent → 响应
                ↑                              ↓
                └──────────────────────────────┘
```

1. 用户在 QQ 中 @机器人 发送消息
2. NoneBot2 通过 WebSocket 将消息发送到 Moltbot Gateway
3. Moltbot 调用 AI Agent 处理消息
4. AI 响应通过 WebSocket 返回给 NoneBot2
5. NoneBot2 将响应发送回 QQ

## 协议说明

### 连接协议

Moltbot Gateway 使用 WebSocket 协议，消息格式为 JSON：

**请求帧 (RequestFrame)**:
```json
{
  "type": "req",
  "id": "uuid",
  "method": "方法名",
  "params": {}
}
```

**响应帧 (ResponseFrame)**:
```json
{
  "type": "res",
  "id": "uuid",
  "ok": true,
  "payload": {}
}
```

**事件帧 (EventFrame)**:
```json
{
  "type": "event",
  "event": "事件名",
  "payload": {}
}
```

### 主要方法

- `connect`: 连接握手
- `chat.send`: 发送聊天消息
- `chat.history`: 获取聊天历史
- `agent`: 调用 AI Agent
- `send`: 发送消息到指定渠道

## 注意事项

1. 确保 Moltbot Gateway 在 NoneBot2 启动前已运行
2. 如果 Gateway 配置了认证，需要在配置中提供正确的 token 或 password
3. 会话使用 `qq:group:{群号}:{用户ID}` 或 `qq:private:{用户ID}` 作为会话标识
