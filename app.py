import os
import streamlit as st
from agent.react_agent import ReactAgent
from agent.tools.agent_tools import rag as rag_service
from utils.bootstrap import validate_runtime
from utils.chat_session_store import (
    create_session, delete_session, load_sessions, save_sessions,
    sort_sessions, update_session_messages, upsert_session,
)
from utils.file_handler import clean_text, pdf_loader
from utils.logger_handler import logger
from utils.path_tool import get_abs_path
from dotenv import load_dotenv
import re

st.set_page_config(page_title="扫地机器人智能客服", page_icon="🤖", layout="wide")

load_dotenv()

runtime_issues = validate_runtime()
if runtime_issues:
    for issue in runtime_issues:
        st.error(issue)
    st.stop()

if "agent" not in st.session_state:
    st.session_state["agent"] = ReactAgent()
if "sessions" not in st.session_state:
    sessions = sort_sessions(load_sessions())
    if not sessions:
        sessions = [create_session()]
        save_sessions(sessions)
    st.session_state["sessions"] = sessions
if "current_session_id" not in st.session_state:
    st.session_state["current_session_id"] = st.session_state["sessions"][0]["id"]
if "pending_prompt" not in st.session_state:
    st.session_state["pending_prompt"] = ""

# 按 ID 找到当前会话，找不到则兜底建新的
def get_current_session() -> dict:
    current_session_id = st.session_state["current_session_id"]
    for session in st.session_state["sessions"]:
        if session["id"] == current_session_id:
            return session
    fallback = create_session()
    st.session_state["sessions"] = [fallback] + st.session_state["sessions"]
    st.session_state["current_session_id"] = fallback["id"]
    save_sessions(sort_sessions(st.session_state["sessions"]))
    return fallback

# 把当前消息列表写回 session + JSON
def persist_current_messages(messages: list[dict]) -> None:
    current = get_current_session()
    updated = update_session_messages(current, messages)
    st.session_state["sessions"] = sort_sessions(upsert_session(st.session_state["sessions"],
    updated))
    st.session_state["current_session_id"] = updated["id"]
    save_sessions(st.session_state["sessions"])

# 切换会话并清除待发 prompt
def switch_session(session_id: str) -> None:
    st.session_state["current_session_id"] = session_id
    st.session_state["pending_prompt"] = ""

# 建新会话并切换
def create_new_chat() -> None:
    new_session = create_session()
    st.session_state["sessions"] = sort_sessions(upsert_session(st.session_state["sessions"],
    new_session))
    st.session_state["current_session_id"] = new_session["id"]
    st.session_state["pending_prompt"] = ""
    save_sessions(st.session_state["sessions"])

# 删除当前会话，删光了自动补一个
def delete_current_chat() -> None:
    current_id = st.session_state["current_session_id"]
    sessions = delete_session(st.session_state["sessions"], current_id)
    if not sessions:
        sessions = [create_session()]
    sessions = sort_sessions(sessions)
    st.session_state["sessions"] = sessions
    st.session_state["current_session_id"] = sessions[0]["id"]
    st.session_state["pending_prompt"] = ""
    save_sessions(sessions)

# 用正则把回答正文和"参考来源"拆开
def split_response_and_references(content: str) -> tuple[str, list[str]]:
    if not content:
        return "", []
    match = re.search(r"\n参考来源：\s*\n(?P<refs>(?:- .+\n?)*)$", content.strip())
    if not match:
        return content.strip(), []
    body = content[: match.start()].strip()
    refs_block = match.group("refs")
    references = [line[2:].strip() for line in refs_block.splitlines() if line.startswith("- ")]
    return body, references

# 渲染为可点击标签 + 可展开预览
def render_references(references: list[str]):
    if not references:
        return
    chips = "".join(f'<span class="ref-chip">{r}</span>' for r in references)
    st.markdown(f'<div class="ref-wrap"><div class="ref-title">参考来源</div><div>{chips}</div></div>',
    unsafe_allow_html=True)
    for index, reference in enumerate(references, start=1):
        with st.expander(f"查看片段 {index}: {reference}", expanded=False):
            st.caption("命中的本地知识片段预览")
            st.write(load_reference_preview(reference))

# 解析 "文件名 第X页" 格式
def parse_reference_label(reference: str) -> tuple[str, int | None]:
    match = re.match(r"^(?P<source>.+?)(?: 第(?P<page>\d+)页)?$", reference.strip())
    if not match:
        return reference.strip(), None
    source = match.group("source").strip()
    page = match.group("page")
    return source, int(page) - 1 if page else None

# 读本地文件取预览片段（@st.cache_data 缓存）
@st.cache_data(show_spinner=False)
def load_reference_preview(reference: str) -> str:
    source, page = parse_reference_label(reference)
    abs_path = get_abs_path(f"data/{source}")
    print(f"[DEBUG] reference={reference}, source={source}, page={page}, abs_path={abs_path}")
    print(f"[DEBUG] abs_path exists={os.path.exists(abs_path) if abs_path else 'N/A'}")
    if not abs_path or not source:
        return "未能解析参考来源。"
    try:
        if source.lower().endswith(".txt"):
            with open(abs_path, "r", encoding="utf-8") as f:
                return clean_text(f.read())[:420] or "该文本来源没有可展示的预览内容。"
        if source.lower().endswith(".pdf"):
            docs = pdf_loader(abs_path)
            if page is not None and 0 <= page < len(docs):
                return clean_text(docs[page].page_content)[:420] or "该页没有可展示内容。"
            if docs:
                return clean_text(docs[0].page_content)[:420] or "PDF 没有可展示内容。"
    except FileNotFoundError:
        return f"本地未找到来源文件：{source}"
    except Exception as e:
        logger.warning(f"加载参考片段失败：{reference}, error={str(e)}")
        return f"无法读取该来源的片段预览：{source}"
    return f"当前仅支持预览 txt/pdf 来源，文件：{source}"

# 统一渲染一条消息
def render_message(message: dict):
    print(f"[DEBUG] message content: {repr(message['content'][:200])}")  # ← 加这行
    body, references = split_response_and_references(message["content"])
    print(f"[DEBUG] body: {repr(body[:100])}")  # ← 加这行
    print(f"[DEBUG] references: {references}")  # ← 加这行
    st.write(body or message["content"])
    render_references(references)

with st.sidebar:
    st.markdown("## 会话管理")
    col1, col2 = st.columns(2)
    if col1.button("新建会话", use_container_width=True):
        create_new_chat()
        st.rerun()
    if col2.button("删除当前", use_container_width=True):
        delete_current_chat()
        st.rerun()
    st.caption("历史会话")
    current_session = get_current_session()
    for session in st.session_state["sessions"]:
        label = session["title"] or "新对话"
        is_current = session["id"] == current_session["id"]
        if st.button(label, key=f"session_{session['id']}", use_container_width=True,
                     type="primary" if is_current else "secondary"):
            switch_session(session["id"])
            st.rerun()

st.markdown("""<div class="hero-wrap"><h1 class="hero-title">扫地机器人智能客服</h1></div>""",
unsafe_allow_html=True)

st.write("")
action_cols = st.columns([1, 1, 4])
if action_cols[0].button("清空会话"):
    persist_current_messages([])
    st.session_state["pending_prompt"] = ""
    st.rerun()
if action_cols[1].button("重建知识库"):
    try:
        with st.spinner("正在重建知识库，请稍候..."):
            rag_service.vector_store.reset_store()
            rag_service.vector_store.load_document()
            rag_service._collection_ready_checked = True
        st.success("知识库重建完成。")
    except Exception as e:
        logger.error(f"知识库重建失败：{str(e)}", exc_info=True)
        st.error("知识库重建失败，请查看日志。")

shortcut_cols = st.columns(3)
shortcuts = ["我家适合买扫拖一体还是纯扫地？", "机器人不回充了怎么排查？",
  "怎么做日常维护延长寿命？"]
for col, text in zip(shortcut_cols, shortcuts):
    if col.button(text):
        st.session_state["pending_prompt"] = text

current_session = get_current_session()
current_messages = current_session.get("messages", [])
if not current_messages:
    st.info("可以先试试上面的快捷问题，也可以直接在下方输入你的需求。")
for message in current_messages:
    avatar = "🧑" if message["role"] == "user" else "🤖"
    with st.chat_message(message["role"], avatar=avatar):
        render_message(message)

input_prompt = st.chat_input("请输入你的问题，例如：拖地有水痕怎么处理？")
prompt = input_prompt or st.session_state.get("pending_prompt", "")
if prompt:
    st.session_state["pending_prompt"] = ""

    with st.chat_message("user", avatar="🧑"):
        st.write(prompt)

    current_messages = current_messages + [{"role": "user", "content": prompt}]
    persist_current_messages(current_messages)

    response_chunks = []

    def capture(generator, cache_list, placeholder):
        for chunk in generator:
            cache_list.append(chunk)
            body, _ = split_response_and_references("".join(cache_list))
            placeholder.markdown(body or "".join(cache_list))
            yield chunk

    try:
        with st.spinner("正在分析问题并检索答案..."):
            res_stream = st.session_state["agent"].execute_stream(current_messages)
            with st.chat_message("assistant", avatar="🤖"):
                response_placeholder = st.empty()
                for _ in capture(res_stream, response_chunks, response_placeholder):
                    pass

            response_text = "".join(response_chunks).strip()
            if not response_text:
                response_text = "暂时没有生成有效回答，请重试。"
            else:
                body, references = split_response_and_references(response_text)
                response_placeholder.markdown(body or response_text)
                render_references(references)
    except Exception as e:
        logger.error(f"对话处理失败：{str(e)}", exc_info=True)
        response_text = "服务暂时不可用，请稍后重试。"
        with st.chat_message("assistant", avatar="🤖"):
            st.write(response_text)

    current_messages = current_messages + [{"role": "assistant", "content": response_text}]
    persist_current_messages(current_messages)
    st.rerun()
