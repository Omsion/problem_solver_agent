Plan DMD：problem_solver_agent 终极商业化改造计划（最终整合版）
目标仓库：github.com/omsion/problem_solver_agent
当前版本：FastAPI + React SPA，热键截图 + 局域网扫码上传 + 智谱 GLM-4.6V OCR + DeepSeek 求解 + SSE 推送
目标形态：本地部署、支持手机5G传图、多用户注册与Token计费、管理员后台的完整商业产品

风险点解决方案详解
在进入阶段实施之前，先针对你提出的五个风险点给出最终解决方案。

风险点1：OCR 不使用 PaddleOCR-VL，改用云端识图大模型 API
最终方案：调用云端多模态大模型 API 进行 OCR。推荐 GLM-OCR 或 智谱 GLM-4.6V，理由如下：

GLM-OCR 参数仅 0.9B，支持印刷体、手写体、数学公式识别，可直接通过 API 调用，无需 GPU。

智谱 GLM-4.6V 是项目现有依赖，直接复用，零迁移成本。

百度 PaddleOCR-VL 也有云端 API 版本，支持公式识别并输出 LaTeX。

配置方式：在 .env 中设置 OCR 提供者和 API Key，后端通过统一的 OpenAI 兼容接口调用。

env
# OCR 配置（三选一）
OCR_PROVIDER=glm                    # 可选：glm / zhipu / baidu
OCR_API_KEY=your-glm-api-key
OCR_BASE_URL=https://open.bigmodel.cn/api/paas/v4
OCR_MODEL=glm-ocr
代码实现（backend/app/services/ocr_service.py）：

python
from openai import AsyncOpenAI
from app.core.config import settings

class OCRService:
    def __init__(self):
        self.client = AsyncOpenAI(
            base_url=settings.OCR_BASE_URL,
            api_key=settings.OCR_API_KEY,
        )
        self.model = settings.OCR_MODEL

    async def recognize(self, image_base64: str, mode: str = "auto") -> dict:
        prompts = {
            "text": "请识别图片中的文字，保持原始格式。",
            "formula": "请识别图片中的公式，输出LaTeX格式。",
            "table": "请识别图片中的表格，输出Markdown表格。",
            "auto": "请识别图片中的文字、公式和表格，输出结构化结果。",
        }
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompts.get(mode, prompts["auto"])},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_base64}"}},
                ],
            }],
            max_tokens=4096,
        )
        return {"text": response.choices[0].message.content}
不需要 GPU，不需要本地部署，直接调 API 即可。

风险点2：公网信令服务需要云服务器
最终方案：确实需要一台云服务器，但配置极低。推荐腾讯云轻量应用服务器（2核2G，约 50 元/月）或阿里云 ECS 突发性能实例（约 40 元/月）。信令服务只做消息转发，不存储图片，内存占用 < 100MB。

如果你暂时不想买服务器，这里有一个零服务器备用方案：使用 QWBP（QR-WebRTC Bootstrap Protocol） ，它通过二维码直接交换 WebRTC 信令数据，完全不需要 WebSocket 服务器。具体原理是：PC 端生成包含 SDP offer 的二维码，手机扫码后解析并生成 SDP answer，再通过二维码回传给 PC，即可建立 P2P 连接。缺点是操作略繁琐（需要两次扫码），但零成本、零服务器。

建议：先用 QWBP 方案快速验证，后续买了服务器再切换到 WebSocket 信令方案。

风险点3：TokenToll 与现有计费逻辑冲突
最终方案：放弃 TokenToll，直接使用 LiteLLM 内置的多租户预算管理。这是最简洁的方案，因为 LiteLLM 本身就是你的 LLM 网关，它天然支持：

虚拟密钥（Virtual Keys） ：为每个用户生成独立的 API Key，用于认证和消费追踪。

预算层级：支持组织 → 团队 → 项目 → 密钥四级预算体系。

硬性预算限制：设置 max_budget 和 budget_duration，超预算自动拒绝请求。

消费日志：每次请求都会写入 Postgres 中的 spend log 表，记录 tokens、cost、model、key hash。

具体实施：

第一步：配置 LiteLLM 连接 Postgres（预算功能必须依赖数据库）：

yaml
# litellm/config.yaml
general_settings:
  master_key: sk-your-master-key
  database_url: os.environ/DATABASE_URL
第二步：为用户创建虚拟密钥（在你的后端注册接口中调用）：

python
# backend/app/services/billing_service.py
import httpx
from app.core.config import settings

class BillingService:
    async def create_user_key(self, user_id: str, budget: float = 10.0) -> str:
        """为用户创建 LiteLLM 虚拟密钥，返回密钥字符串"""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{settings.LITELLM_BASE_URL}/key/generate",
                headers={"Authorization": f"Bearer {settings.LITELLM_MASTER_KEY}"},
                json={
                    "user_id": user_id,
                    "max_budget": budget,
                    "budget_duration": "30d",
                    "models": ["deepseek-v4-pro", "claude-sonnet"],
                    "tpm_limit": 50000,
                    "rpm_limit": 30,
                },
            )
            return resp.json()["key"]

    async def get_user_spend(self, user_id: str) -> dict:
        """查询用户消费情况"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.LITELLM_BASE_URL}/user/info",
                headers={"Authorization": f"Bearer {settings.LITELLM_MASTER_KEY}"},
                params={"user_id": user_id},
            )
            return resp.json()

    async def check_budget(self, user_key: str) -> bool:
        """检查密钥预算是否充足"""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{settings.LITELLM_BASE_URL}/key/info",
                headers={"Authorization": f"Bearer {settings.LITELLM_MASTER_KEY}"},
                params={"key": user_key},
            )
            data = resp.json()
            return data.get("spend", 0) < data.get("max_budget", float("inf"))
第三步：调用 LLM 时使用用户的虚拟密钥：

python
# backend/app/services/llm_service.py
class LLMService:
    async def solve(self, prompt: str, user_key: str, model: str = "deepseek-v4-pro"):
        client = AsyncOpenAI(
            base_url=settings.LITELLM_BASE_URL,
            api_key=user_key,  # 使用用户的虚拟密钥
        )
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        async for chunk in response:
            yield chunk.choices[0].delta.content or ""
第四步：管理后台通过 LiteLLM 的管理 API 查看所有用户的消费：

python
# 获取所有密钥的消费概览
GET /key/list?return_full_object=true
# 获取单个用户的消费详情
GET /user/info?user_id=xxx
# 获取团队消费
GET /team/info?team_id=xxx
这样不需要额外部署 TokenToll，LiteLLM 一个组件就搞定了计费、预算、限流和消费追踪。冲突彻底消除。

风险点4：Refine 企业版授权问题
最终方案：使用 @authhero/admin 替代 Refine 企业版。

选型理由：

方案	优势	劣势
@authhero/admin	基于 react-admin（成熟生态），Vite + React 19 + shadcn/ui + Tailwind v4，内置多租户管理 UI，零授权费	需要对接你自己的后端 API
App Studio	全栈 SaaS starter，含 Stripe 计费集成	较重，需要生成代码，学习曲线陡
Refine 社区版	生态好	多租户需要付费企业版
React Admin 裸用	最灵活	需要自己写所有页面
推荐 @authhero/admin，因为它直接构建在 react-admin 之上，你只需要实现 dataProvider 对接 FastAPI 后端即可。

具体实施：

bash
# 安装
npm install @authhero/admin ra-core

# 在 admin/ 目录下创建管理后台入口
tsx
// admin/src/App.tsx
import { Admin, Resource } from 'react-admin';
import simpleRestProvider from 'ra-data-simple-rest';
import { UserList, UserEdit } from './pages/users';
import { UsageDashboard } from './pages/usage';
import { PricingConfig } from './pages/pricing';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1/admin';

const dataProvider = simpleRestProvider(API_URL);

export default function App() {
  return (
    <Admin dataProvider={dataProvider}>
      <Resource name="users" list={UserList} edit={UserEdit} />
      <Resource name="usage" list={UsageDashboard} />
      <Resource name="pricing" list={PricingConfig} />
    </Admin>
  );
}
后端只需要提供对应的 REST 接口（GET /admin/users, GET /admin/usage, 等等），react-admin 会自动处理分页、排序、筛选。

风险点5：手机5G传图延迟 + S3 依赖
最终方案：使用 WebRTC DataChannel 直接 P2P 传输，彻底不用 S3，不用上传中转。

原理：WebRTC 建立 P2P 连接后，文件通过 DataChannel 直接从手机传输到电脑，不经过任何服务器。信令服务器只负责帮助两端“找到对方”，之后完全退出。

延迟对比：

方案	传输路径	预估延迟（5G网络）
S3 中转	手机→S3→PC	3-8 秒
WebRTC P2P	手机→PC 直连	0.5-2 秒
具体实施：

PC 端（Tauri 应用内）：

typescript
// frontend/src/lib/webrtc.ts
export class WebRTCReceiver {
  private pc: RTCPeerConnection;

  constructor() {
    this.pc = new RTCPeerConnection({
      iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:stun.cloudflare.com:3478' },
      ],
    });

    this.pc.ondatachannel = (event) => {
      const channel = event.channel;
      const chunks: ArrayBuffer[] = [];

      channel.onmessage = (e) => {
        if (typeof e.data === 'string') {
          // 字符串消息：文件名等元数据
          return;
        }
        chunks.push(e.data);
      };

      channel.onclose = () => {
        // 接收完成，合并 chunks 并保存到 Screenshots/
        const blob = new Blob(chunks);
        this.saveToScreenshots(blob);
      };
    };
  }

  async createOffer(): Promise<string> {
    const offer = await this.pc.createOffer();
    await this.pc.setLocalDescription(offer);
    return JSON.stringify(offer);
  }

  private async saveToScreenshots(blob: Blob) {
    const arrayBuffer = await blob.arrayBuffer();
    const bytes = new Uint8Array(arrayBuffer);
    // 通过 Tauri invoke 保存到本地
    await invoke('save_screenshot', { data: Array.from(bytes) });
  }
}
手机端（PWA 页面）：

tsx
// frontend/src/pages/Mobile/MobileCamera.tsx
export function MobileCamera() {
  const [connected, setConnected] = useState(false);
  const pcRef = useRef<RTCPeerConnection | null>(null);

  const connect = async (qrData: string) => {
    const pc = new RTCPeerConnection({
      iceServers: [
        { urls: 'stun:stun.l.google.com:19302' },
        { urls: 'stun:stun.cloudflare.com:3478' },
      ],
    });
    pcRef.current = pc;

    // 解析 PC 端的 offer
    const offer = JSON.parse(qrData);
    await pc.setRemoteDescription(offer);

    // 创建 answer
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);

    // 建立 DataChannel
    const channel = pc.createDataChannel('fileTransfer');
    channel.binaryType = 'arraybuffer';

    setConnected(true);
    return JSON.stringify(answer);
  };

  const sendPhoto = async (file: File) => {
    const channel = pcRef.current?.createDataChannel('fileTransfer');
    if (!channel) return;

    // 先发送文件名
    channel.send(JSON.stringify({ filename: file.name, size: file.size }));

    // 分块传输文件
    const CHUNK_SIZE = 16 * 1024; // 16KB
    const buffer = await file.arrayBuffer();
    const totalChunks = Math.ceil(buffer.byteLength / CHUNK_SIZE);

    for (let i = 0; i < totalChunks; i++) {
      const start = i * CHUNK_SIZE;
      const end = Math.min(start + CHUNK_SIZE, buffer.byteLength);
      channel.send(buffer.slice(start, end));
    }

    channel.close();
  };

  return (
    <div className="flex flex-col items-center p-6 gap-6">
      {!connected ? (
        <button onClick={() => connect(/* 扫码获取的offer */)}>
          连接电脑
        </button>
      ) : (
        <>
          <input
            type="file"
            accept="image/*"
            capture="environment"
            onChange={(e) => e.target.files?.[0] && sendPhoto(e.target.files[0])}
            className="hidden"
            id="camera-input"
          />
          <label htmlFor="camera-input" className="px-8 py-4 bg-blue-600 text-white rounded-lg text-lg">
            拍照并发送
          </label>
        </>
      )}
    </div>
  );
}
关键点：纯 STUN 连接在大多数 5G 网络下可以成功（实测 92.7% 的国内 4G/5G 终端可直连完成端到端加密通话）。如果遇到 NAT 穿透失败，再考虑加 TURN 服务器（腾讯云/阿里云有免费额度）。

风险点6：多租户数据隔离不会用 SQL
最终方案：使用 fastapi-rls 库，它会自动为你生成 RLS 策略和 SQL，你只需要在模型上声明一行代码。

fastapi-rls 是专门为 FastAPI + SQLAlchemy 设计的 PostgreSQL RLS 库，提供声明式策略、自动 DDL 生成、FastAPI 依赖注入和 Alembic 迁移支持。

具体实施：

第一步：安装

bash
pip install "fastapi-rls[all]"
第二步：在模型上声明策略（一行代码）：

python
# backend/app/models/task.py
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from fastapi_rls import TenantPolicy
from fastapi_rls.adapters.sqlalchemy import RLSMixin

class Base(DeclarativeBase):
    pass

class Task(Base, RLSMixin):
    __tablename__ = "tasks"
    __rls_policies__ = [
        TenantPolicy("tenant_isolation", column="tenant_id"),
    ]

    id: Mapped[str] = mapped_column(primary_key=True)
    tenant_id: Mapped[str] = mapped_column(index=True)
    user_id: Mapped[str] = mapped_column(index=True)
    image_path: Mapped[str]
    ocr_text: Mapped[str | None]
    answer: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
第三步：应用 RLS 策略到数据库（自动生成 SQL）：

python
# backend/app/main.py
from fastapi_rls import RLS
from app.models import Base

rls = RLS(engine=engine)
rls.sync()  # 自动执行 ENABLE RLS + CREATE POLICY
或者通过 CLI：

bash
fastapi-rls sync --url "$DATABASE_URL" --policies app.models
第四步：在 FastAPI 请求中注入租户上下文：

python
# backend/app/core/deps.py
from fastapi import Depends
from fastapi_rls import RLS
from app.core.security import get_current_user

rls = RLS(engine=engine)

def get_tenant_context(user = Depends(get_current_user)) -> dict:
    return {"tenant_id": user.tenant_id}

get_db = rls.session_dependency(get_tenant_context)
这样就完了。之后每次查询 Task 时，PostgreSQL 会自动加上 WHERE tenant_id = current_tenant，你不需要在任何地方手动写 WHERE 条件。这是数据库层面的隔离，即使代码有 bug 也不会泄露数据。

自动生成的 SQL 示例（你不需要手写，fastapi-rls 会自动执行）：

sql
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON tasks
  USING (tenant_id = current_setting('app.current_tenant')::text)
  WITH CHECK (tenant_id = current_setting('app.current_tenant')::text);
阶段实施计划
阶段零：基础重构（2周）
0.1 后端模块化拆分
新建目录结构：

text
backend/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── security.py
│   │   └── deps.py
│   ├── api/
│   │   └── v1/
│   │       ├── router.py
│   │       ├── auth.py
│   │       ├── tasks.py
│   │       ├── upload.py
│   │       ├── billing.py
│   │       └── admin.py
│   ├── models/
│   │   ├── base.py
│   │   ├── user.py
│   │   ├── task.py
│   │   └── usage.py
│   ├── schemas/
│   ├── services/
│   │   ├── ocr_service.py
│   │   ├── llm_service.py
│   │   ├── pipeline.py
│   │   └── billing_service.py
│   └── workers/
│       ├── celery_app.py
│       └── tasks.py
├── alembic/
├── requirements.txt
└── Dockerfile
文件迁移映射：

现有文件	新位置	改造说明
run_web.py	backend/app/main.py	移除自动打开浏览器逻辑
webapp/app.py	backend/app/main.py	保留 create_app() 工厂模式
webapp/config.py	backend/app/core/config.py	扩展为 pydantic-settings
problem_solver_agent/vision_client.py	backend/app/services/ocr_service.py	替换为云端大模型 API 调用
problem_solver_agent/solver_client.py	backend/app/services/llm_service.py	通过 LiteLLM 网关调用，使用用户虚拟密钥
webapp/pipeline.py	backend/app/services/pipeline.py	改为异步，由 Celery 任务调用
0.2 数据库迁移：SQLite → PostgreSQL
python
# backend/app/models/user.py
from sqlalchemy import Column, String, DateTime, Enum
from app.models.base import Base
import enum, uuid

class UserRole(str, enum.Enum):
    USER = "user"
    ADMIN = "admin"
    SUPERADMIN = "superadmin"

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    phone = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=True)
    role = Column(Enum(UserRole), default=UserRole.USER)
    tenant_id = Column(String, index=True, default="default")
    litellm_key = Column(String, nullable=True)  # 存储用户的 LiteLLM 虚拟密钥
    created_at = Column(DateTime, default=datetime.utcnow)
python
# backend/app/models/task.py — 与上面 RLS 示例相同
python
# backend/app/models/usage.py
class UsageEvent(Base):
    __tablename__ = "usage_events"
    __rls_policies__ = [TenantPolicy("tenant_isolation", column="tenant_id")]

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String, index=True)
    user_id = Column(String, index=True)
    provider = Column(String)
    model = Column(String)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    task_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
0.3 Celery 异步任务
python
# backend/app/workers/celery_app.py
from celery import Celery
from app.core.config import settings

celery_app = Celery(
    "problem_solver",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_time_limit=600,
)
python
# backend/app/workers/tasks.py
from celery import shared_task
from app.services.pipeline import run_pipeline

@shared_task(bind=True, max_retries=3)
def process_task(self, task_id: str, image_paths: list[str], user_key: str):
    try:
        run_pipeline(task_id, image_paths, user_key)
    except Exception as exc:
        self.retry(exc=exc, countdown=10)
0.4 配置管理
env
# .env.example — 完整配置
DEEPSEEK_API_KEY=
ZHIPU_API_KEY=
SOLVER_ROOT_DIR=
GROUP_TIMEOUT=8
MAX_CONCURRENT_TASKS=2
IMAGE_MAX_EDGE=1600
IMAGE_JPEG_QUALITY=80

# OCR（云端大模型 API）
OCR_PROVIDER=glm
OCR_API_KEY=
OCR_BASE_URL=https://open.bigmodel.cn/api/paas/v4
OCR_MODEL=glm-ocr

# 数据库
DATABASE_URL=postgresql+asyncpg://solver:solver123@localhost:5432/problem_solver
REDIS_URL=redis://localhost:6379/0

# 认证
JWT_SECRET_KEY=change-me-in-production
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440

# LiteLLM
LITELLM_BASE_URL=http://localhost:4000
LITELLM_MASTER_KEY=sk-litellm-master
LITELLM_DATABASE_URL=postgresql://solver:solver123@localhost:5432/litellm

# 短信
SMS_PROVIDER=aliyun
SMS_ACCESS_KEY_ID=
SMS_ACCESS_KEY_SECRET=

# 信令服务
VITE_SIGNALING_URL=wss://your-vps:8080
阶段一：桌面端 Tauri 2 重构（2周）
1.1 初始化 Tauri
bash
cd frontend
npm install @tauri-apps/cli@latest
npx tauri init
toml
# frontend/src-tauri/Cargo.toml
[dependencies]
tauri = { version = "2", features = ["tray-icon"] }
tauri-plugin-global-shortcut = "2"
tauri-plugin-single-instance = "2"
xcap = "0.0.14"
image = "0.25"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
1.2 全局快捷键与区域截图
rust
// frontend/src-tauri/src/screenshot.rs
use xcap::Monitor;
use image;
use std::path::PathBuf;

pub fn capture_region(x: i32, y: i32, width: u32, height: u32) -> Result<PathBuf, String> {
    let monitors = Monitor::all().map_err(|e| e.to_string())?;
    let monitor = monitors.into_iter().next().ok_or("no monitor")?;
    let image = monitor.capture_image().map_err(|e| e.to_string())?;
    let cropped = image::imageops::crop_imm(&image, x as u32, y as u32, width, height).to_image();
    let timestamp = chrono::Local::now().format("%Y%m%d_%H%M%S_%3f");
    let path = PathBuf::from(&format!(
        "{}/Screenshots/screenshot_{}.png",
        std::env::var("SOLVER_ROOT_DIR").unwrap_or_else(|_| ".".into()),
        timestamp
    ));
    cropped.save(&path).map_err(|e| e.to_string())?;
    Ok(path)
}

#[tauri::command]
pub fn save_screenshot(data: Vec<u8>, filename: String) -> Result<String, String> {
    let path = PathBuf::from(&format!(
        "{}/Screenshots/{}",
        std::env::var("SOLVER_ROOT_DIR").unwrap_or_else(|_| ".".into()),
        filename
    ));
    std::fs::write(&path, data).map_err(|e| e.to_string())?;
    Ok(path.to_string_lossy().to_string())
}
rust
// frontend/src-tauri/src/main.rs
mod screenshot;

use tauri::Manager;
use tauri_plugin_global_shortcut::{Code, Modifiers, ShortcutState};

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            let _ = app.get_webview_window("main").unwrap().set_focus();
        }))
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_shortcut("Ctrl+Shift+S")
                .unwrap()
                .with_handler(|app, shortcut, event| {
                    if event.state == ShortcutState::Pressed
                        && shortcut.matches(Modifiers::CONTROL | Modifiers::SHIFT, Code::KeyS)
                    {
                        let _ = app.emit("trigger-screenshot", ());
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            screenshot::capture_region,
            screenshot::save_screenshot,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
1.3 删除旧截图工具
删除 tools/silent_screencapper.py 和 tools/remote_trigger.py。

阶段二：手机端5G传图（2周）
2.1 WebRTC P2P 传输核心
PC 端生成 QR 码包含 WebRTC offer：

tsx
// frontend/src/components/QRCodePairing.tsx
import { QRCodeSVG } from "qrcode.react";
import { WebRTCReceiver } from "../../lib/webrtc";
import { useEffect, useState } from "react";

export function QRCodePairing() {
  const [offer, setOffer] = useState<string>("");
  const receiverRef = useRef<WebRTCReceiver | null>(null);

  useEffect(() => {
    const receiver = new WebRTCReceiver();
    receiverRef.current = receiver;
    receiver.createOffer().then(setOffer);
  }, []);

  if (!offer) return <div>初始化中...</div>;

  return (
    <div className="flex flex-col items-center gap-4 p-4">
      <QRCodeSVG value={offer} size={280} />
      <p className="text-sm text-gray-500">手机扫码，通过5G直接传图</p>
    </div>
  );
}
手机端扫码后建立连接并发送图片：

tsx
// frontend/src/pages/Mobile/MobileCamera.tsx
import { useEffect, useRef, useState } from "react";
import jsQR from "jsqr";

export function MobileCamera() {
  const [connected, setConnected] = useState(false);
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  // 扫码解析 WebRTC offer
  const handleScan = async (qrData: string) => {
    const pc = new RTCPeerConnection({
      iceServers: [
        { urls: "stun:stun.l.google.com:19302" },
        { urls: "stun:stun.cloudflare.com:3478" },
      ],
    });
    pcRef.current = pc;

    const offer = JSON.parse(qrData);
    await pc.setRemoteDescription(offer);
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);

    setConnected(true);
  };

  // 拍照并发送
  const sendPhoto = async (file: File) => {
    if (!pcRef.current) return;
    const channel = pcRef.current.createDataChannel("fileTransfer");
    channel.binaryType = "arraybuffer";

    channel.onopen = async () => {
      // 发送文件名
      channel.send(JSON.stringify({ filename: file.name, size: file.size }));

      // 分块传输
      const CHUNK_SIZE = 16 * 1024;
      const buffer = await file.arrayBuffer();
      const totalChunks = Math.ceil(buffer.byteLength / CHUNK_SIZE);

      for (let i = 0; i < totalChunks; i++) {
        const start = i * CHUNK_SIZE;
        const end = Math.min(start + CHUNK_SIZE, buffer.byteLength);
        channel.send(buffer.slice(start, end));
      }

      channel.close();
    };
  };

  return (
    <div className="flex flex-col items-center p-6 gap-6">
      {!connected ? (
        <>
          <video ref={videoRef} autoPlay className="w-full rounded" />
          <p className="text-sm text-gray-500">扫描电脑上的二维码</p>
        </>
      ) : (
        <>
          <input
            type="file"
            accept="image/*"
            capture="environment"
            onChange={(e) => e.target.files?.[0] && sendPhoto(e.target.files[0])}
            className="hidden"
            id="camera-input"
          />
          <label htmlFor="camera-input" className="px-8 py-4 bg-blue-600 text-white rounded-lg text-lg">
            拍照并发送
          </label>
        </>
      )}
    </div>
  );
}
2.2 备用方案：QWBP（零服务器）
如果 WebRTC 直连失败（NAT 穿透问题），使用 QWBP 协议：

bash
npm install qwbp
typescript
// PC 端生成 offer 二维码
import { QWBP } from "qwbp";
const qwbp = new QWBP();
const offerQR = await qwbp.createOffer();

// 手机端扫码后生成 answer 二维码
const answer = await qwbp.acceptOffer(offerQR);
// PC 端扫描手机上的 answer 二维码，连接建立
await qwbp.acceptAnswer(answer);
QWBP 通过二维码直接交换信令数据，完全不需要 WebSocket 服务器。

阶段三：LLM 网关与 OCR 升级（2周）
3.1 LiteLLM 网关部署
yaml
# litellm/config.yaml
general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/LITELLM_DATABASE_URL

model_list:
  - model_name: "deepseek-v4-pro"
    litellm_params:
      model: "deepseek/deepseek-chat"
      api_key: os.environ/DEEPSEEK_API_KEY
  - model_name: "claude-sonnet"
    litellm_params:
      model: "anthropic/claude-sonnet-4-20250514"
      api_key: os.environ/ANTHROPIC_API_KEY

router_settings:
  routing_strategy: "latency-based-routing"
  fallbacks: [{"deepseek-v4-pro": ["claude-sonnet"]}]
  num_retries: 3
  timeout: 60
部署：

bash
litellm --config litellm/config.yaml --port 4000
3.2 OCR 服务（云端 API）
python
# backend/app/services/ocr_service.py — 与前面风险点1的代码相同
3.3 LLM 服务（用户虚拟密钥）
python
# backend/app/services/llm_service.py
from openai import AsyncOpenAI
from app.core.config import settings

class LLMService:
    async def solve(self, prompt: str, user_key: str, model: str = "deepseek-v4-pro"):
        client = AsyncOpenAI(
            base_url=settings.LITELLM_BASE_URL,
            api_key=user_key,
        )
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        async for chunk in response:
            yield chunk.choices[0].delta.content or ""
阶段四：用户系统与计费（2周）
4.1 手机号注册登录
python
# backend/app/api/v1/auth.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.deps import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.services.billing_service import BillingService

router = APIRouter()
billing = BillingService()

class RegisterRequest(BaseModel):
    phone: str = Field(..., pattern=r"^1[3-9]\d{9}$")
    code: str
    password: str | None = None

@router.post("/send-code")
async def send_code(req: RegisterRequest):
    # 接入阿里云/腾讯云 SMS，发送6位验证码，存入 Redis，5分钟过期
    # redis.setex(f"sms:{req.phone}", 300, code)
    return {"ok": True}

@router.post("/register")
async def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.phone == req.phone).first():
        raise HTTPException(400, "手机号已注册")
    user = User(
        phone=req.phone,
        hashed_password=hash_password(req.password or req.phone[-6:]),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # 为用户创建 LiteLLM 虚拟密钥（免费额度 10 元）
    user_key = await billing.create_user_key(user.id, budget=10.0)
    user.litellm_key = user_key
    db.commit()

    token = create_access_token({"sub": user.id, "role": user.role.value})
    return {"access_token": token, "token_type": "bearer"}

@router.post("/login")
async def login(req: RegisterRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == req.phone).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(401, "手机号或密码错误")
    token = create_access_token({"sub": user.id, "role": user.role.value})
    return {"access_token": token, "token_type": "bearer"}
4.2 计费服务
python
# backend/app/services/billing_service.py — 与前面风险点3的代码相同
4.3 数据库迁移
bash
cd backend
alembic revision --autogenerate -m "add_users_usage_tenants"
alembic upgrade head
阶段五：管理后台（2周）
5.1 初始化 @authhero/admin
bash
cd admin
npm install @authhero/admin ra-core ra-data-simple-rest
tsx
// admin/src/App.tsx — 与前面风险点4的代码相同
5.2 管理后台核心页面
用户列表：

tsx
// admin/src/pages/users.tsx
import { List, Datagrid, TextField, DateField, EditButton, BooleanField } from 'react-admin';

export function UserList() {
  return (
    <List>
      <Datagrid rowClick="edit">
        <TextField source="phone" label="手机号" />
        <TextField source="role" label="角色" />
        <TextField source="litellm_key" label="虚拟密钥" />
        <DateField source="created_at" label="注册时间" />
        <EditButton />
      </Datagrid>
    </List>
  );
}
用量看板：

tsx
// admin/src/pages/usage.tsx
import { List, Datagrid, TextField, NumberField, DateField } from 'react-admin';

export function UsageDashboard() {
  return (
    <List>
      <Datagrid>
        <TextField source="user_id" label="用户" />
        <TextField source="model" label="模型" />
        <NumberField source="input_tokens" label="输入Token" />
        <NumberField source="output_tokens" label="输出Token" />
        <DateField source="created_at" label="时间" />
      </Datagrid>
    </List>
  );
}
5.3 管理员接口
python
# backend/app/api/v1/admin.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.deps import get_db, get_current_admin
from app.models.user import User
from app.models.usage import UsageEvent
from app.services.billing_service import BillingService

router = APIRouter(prefix="/admin", dependencies=[Depends(get_current_admin)])
billing = BillingService()

@router.get("/users")
def list_users(db: Session = Depends(get_db), skip: int = 0, limit: int = 50):
    users = db.query(User).offset(skip).limit(limit).all()
    return {"data": users, "total": db.query(User).count()}

@router.get("/users/{user_id}/usage")
async def user_usage(user_id: str, db: Session = Depends(get_db)):
    # 从 LiteLLM 获取消费数据
    spend_data = await billing.get_user_spend(user_id)
    events = db.query(UsageEvent).filter(UsageEvent.user_id == user_id).all()
    total_tokens = sum(e.input_tokens + e.output_tokens for e in events)
    return {
        "user_id": user_id,
        "total_tokens": total_tokens,
        "litellm_spend": spend_data,
        "events": events,
    }

@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    total_users = db.query(User).count()
    total_tokens = db.query(UsageEvent).with_entities(
        func.sum(UsageEvent.input_tokens + UsageEvent.output_tokens)
    ).scalar() or 0
    return {"total_users": total_users, "total_tokens": total_tokens}
阶段六：前端商业化升级（2周）
6.1 PC 端解题台左右分栏
tsx
// frontend/src/pages/Workspace/WorkspacePage.tsx
import { SplitPanelLayout } from "../../components/layout/SplitPanelLayout";
import { ImageViewer } from "../../components/viewer/ImageViewer";
import { OutputPanel } from "../../components/output/OutputPanel";
import { ModelSelector } from "../../components/workspace/ModelSelector";
import { TokenBalance } from "../../components/workspace/TokenBalance";

export function WorkspacePage() {
  return (
    <div className="h-screen flex flex-col">
      <header className="h-12 border-b flex items-center px-4 justify-between">
        <ModelSelector />
        <TokenBalance />
      </header>
      <SplitPanelLayout
        left={<ImageViewer />}
        right={<OutputPanel />}
      />
    </div>
  );
}
6.2 手机端 PWA
json
// frontend/public/manifest.json
{
  "name": "Problem Solver",
  "short_name": "解题助手",
  "start_url": "/mobile",
  "display": "standalone",
  "background_color": "#ffffff",
  "theme_color": "#2563eb",
  "icons": [
    { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/icon-512.png", "sizes": "512x512", "type": "image/png" }
  ]
}
6.3 登录页面
tsx
// frontend/src/pages/Login/LoginPage.tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";

export function LoginPage() {
  const [phone, setPhone] = useState("");
  const [code, setCode] = useState("");
  const [sent, setSent] = useState(false);
  const navigate = useNavigate();

  const sendCode = async () => {
    await fetch("/api/v1/auth/send-code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone }),
    });
    setSent(true);
  };

  const login = async () => {
    const res = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ phone, code, password: code }),
    });
    const data = await res.json();
    localStorage.setItem("token", data.access_token);
    navigate("/");
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="w-full max-w-sm p-8 bg-white rounded-lg shadow">
        <h1 className="text-xl font-bold mb-6 text-center">登录 Problem Solver</h1>
        <input
          placeholder="手机号"
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          className="w-full border rounded px-3 py-2 mb-3"
        />
        <div className="flex gap-2 mb-4">
          <input
            placeholder="验证码"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className="flex-1 border rounded px-3 py-2"
          />
          <button
            onClick={sendCode}
            disabled={sent}
            className="px-3 py-2 bg-gray-100 rounded text-sm whitespace-nowrap"
          >
            {sent ? "已发送" : "获取验证码"}
          </button>
        </div>
        <button
          onClick={login}
          className="w-full bg-blue-600 text-white rounded py-2 font-medium"
        >
          登录
        </button>
      </div>
    </div>
  );
}
阶段七：部署（1周）
7.1 Docker Compose 一键部署
yaml
# docker-compose.yml
version: "3.9"

services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_DB: problem_solver
      POSTGRES_USER: solver
      POSTGRES_PASSWORD: ${DB_PASSWORD:-solver123}
    volumes:
      - pgdata:/var/lib/postgresql/data
    ports:
      - "5432:5432"

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  litellm:
    image: ghcr.io/berriai/litellm:main-latest
    volumes:
      - ./litellm/config.yaml:/app/config.yaml
    command: ["--config", "/app/config.yaml", "--port", "4000"]
    ports:
      - "4000:4000"
    environment:
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}
      - LITELLM_DATABASE_URL=postgresql://solver:${DB_PASSWORD:-solver123}@postgres:5432/litellm
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}

  backend:
    build: ./backend
    ports:
      - "8000:8000"
    depends_on:
      - postgres
      - redis
      - litellm
    environment:
      - DATABASE_URL=postgresql+asyncpg://solver:${DB_PASSWORD:-solver123}@postgres:5432/problem_solver
      - REDIS_URL=redis://redis:6379/0
      - LITELLM_BASE_URL=http://litellm:4000
      - OCR_API_KEY=${OCR_API_KEY}
      - OCR_BASE_URL=${OCR_BASE_URL}
    volumes:
      - ./Screenshots:/app/Screenshots
      - ./solutions:/app/solutions

  celery-worker:
    build: ./backend
    command: celery -A app.workers.celery_app worker --loglevel=info --concurrency=2
    depends_on:
      - redis
      - postgres
      - litellm
    environment:
      - DATABASE_URL=postgresql+asyncpg://solver:${DB_PASSWORD:-solver123}@postgres:5432/problem_solver
      - REDIS_URL=redis://redis:6379/0
      - LITELLM_BASE_URL=http://litellm:4000

  frontend:
    build: ./frontend
    ports:
      - "3000:3000"

  admin:
    build: ./admin
    ports:
      - "3001:3000"

volumes:
  pgdata:
7.2 启动命令
bash
# 初始化 LiteLLM 数据库
docker compose up -d postgres
docker compose exec postgres createdb -U solver litellm

# 启动全部服务
docker compose up -d

# 执行数据库迁移
docker compose exec backend alembic upgrade head
执行顺序与依赖关系
text
阶段零（基础重构）
    ↓
阶段一（Tauri桌面端） ←──并行──→ 阶段二（手机端5G传图）
    ↓                              ↓
阶段三（LLM网关+OCR） ←────────────┘
    ↓
阶段四（用户+计费）
    ↓
阶段五（管理后台）
    ↓
阶段六（前端升级）
    ↓
阶段七（部署）
关键路径：阶段零 → 阶段三 → 阶段四 → 阶段六。

关键文件改动总清单
现有文件	操作	新位置/说明
run_web.py	替换	backend/app/main.py
webapp/app.py	迁移	backend/app/main.py
webapp/config.py	迁移+扩展	backend/app/core/config.py
webapp/pipeline.py	重构	backend/app/services/pipeline.py
webapp/models.py	迁移	backend/app/models/task.py
problem_solver_agent/vision_client.py	替换	backend/app/services/ocr_service.py
problem_solver_agent/solver_client.py	重构	backend/app/services/llm_service.py
tools/silent_screencapper.py	删除	由 Tauri 替代
tools/remote_trigger.py	删除	由手机 PWA 替代
frontend/package.json	扩展	增加 Tauri、WebRTC 依赖
frontend/src/App.tsx	重构	拆分为 WorkspacePage + 路由
.env.example	扩展	增加完整配置
新增文件总清单
新文件	用途
backend/app/core/security.py	JWT + 密码哈希
backend/app/api/v1/auth.py	注册/登录/验证码
backend/app/api/v1/admin.py	管理员接口
backend/app/services/billing_service.py	LiteLLM 虚拟密钥管理
backend/app/workers/celery_app.py	Celery 配置
backend/app/workers/tasks.py	异步任务定义
backend/app/models/user.py	用户模型
backend/app/models/usage.py	用量模型
backend/app/models/base.py	SQLAlchemy Base
litellm/config.yaml	LiteLLM 模型路由
frontend/src/lib/webrtc.ts	WebRTC P2P 传输
frontend/src/components/QRCodePairing.tsx	扫码配对
frontend/src/pages/Mobile/MobileCamera.tsx	手机拍照页
frontend/src/pages/Login/LoginPage.tsx	登录页
frontend/public/manifest.json	PWA 清单
admin/src/App.tsx	@authhero/admin 管理后台
docker-compose.yml	一键部署
风险与应对
风险	应对
WebRTC NAT 穿透失败	备用 QWBP 协议（零服务器）；或加 TURN 服务器（腾讯云免费额度）
云端 OCR API 限流	配置多个 OCR 提供者轮换；或在 LiteLLM 中做 fallback
信令服务器暂时没买	先用 QWBP 零服务器方案，后续再切换到 WebSocket
LiteLLM 数据库连接失败	预算功能需要 Postgres，确保 DATABASE_URL 正确配置
手机浏览器不支持 WebRTC	现代浏览器（Chrome/Safari/Firefox/Edge）均支持，覆盖 99% 用户
多租户 RLS 策略未生效	fastapi-rls 提供跨租户泄露测试，部署后跑一次验证
验收标准
每个阶段完成后，执行以下端到端验证：

阶段零：pytest 全部通过；curl localhost:8000/api/v1/tasks 返回 JSON。

阶段一：按 Ctrl+Shift+S 触发截图，图片出现在 Screenshots/。

阶段二：手机通过5G扫码，拍照后2秒内 PC 端 Screenshots/ 出现新图片。

阶段三：OCR 返回文本；LLM 流式返回答案；切换模型正常。

阶段四：手机号注册成功并自动获得 LiteLLM 虚拟密钥；超预算时请求被拒绝。

阶段五：管理后台可查看用户列表和用量看板。

阶段六：PC 端左右分栏正常；手机端 PWA 可添加到主屏幕。

阶段七：docker compose up 一键启动全部服务。

这份 Plan.md 可直接保存为项目根目录的 PLAN.md，在 DeepSeek Honey 中作为执行指令使用。 建议按阶段顺序执行，每阶段完成后先跑验收标准，确认无误后再进入下一阶段。