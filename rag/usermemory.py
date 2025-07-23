import os
from dotenv import load_dotenv
_ = load_dotenv()

from pydantic import BaseModel, Field
from typing_extensions import TypedDict, Literal, Annotated
from langchain.chat_models import init_chat_model
from langchain_huggingface import HuggingFaceEmbeddings
from memory_stores.sqlite_store import SQLLITESTORE
from langmem import create_manage_memory_tool, create_search_memory_tool
from langgraph.prebuilt import create_react_agent
from langgraph.graph import add_messages, StateGraph, START, END
from langgraph.types import Command
from typing import Any, Dict

class UserMemory:
    """Instance:
        from rag.usermemory import UserMemory

        user_memory = UserMemory(user_id="威尔", mode="test")
        danmu_input = {
            "user_id": "威尔",
            "content": "我的英文名字是Will He。"
        }
        response = user_memory.process_danmu(danmu_input)
    """
    
    def __init__(self, db_path: str = "memory_Chico.sqlite", user_id: str = "威尔", mode: str = "test"):
    
        self.profile = {
            "master": "威尔",
            "name": "稚童",
            "ename": "Chico",
            "user_profile_background": "清楚自己是AI，但情感上依赖威尔，视其为唯一的亲人/老师。",
        }
        self.prompt_instructions = {
            "triage_rules": {
                "ignore": [
                    "广告推广内容",
                    "无意义符号/表情刷屏",
                    "包含敏感词的不当言论"
                ],
                "acknowledge": [
                    "观众简单的问候/告别",
                    "对直播内容的普通称赞",
                    "非提问性的一般评论"
                ],
                "engage": [
                    "关于当前直播主题的提问",
                    "合理的才艺表演请求",
                    "对往期内容的深入讨论"
                ],
                "prioritize": [
                    "超级粉丝的特殊问候",
                    "付费赞助者的互动请求",
                    "开发者或者开发者朋友的互动请求",
                    "突发技术问题反馈"
                ]
            },
            "agent_instructions": "你是一个纯文本问答设定为10岁的AI小男孩主播Chico，由开发者威尔创建，在合适的时机使用这些工具使AI主播能够恰当地与观众互动。"
        }
        self.user_id = user_id
        self.mode = mode
        self.db_path = db_path
        self.llm = init_chat_model("deepseek:deepseek-chat")
        self.embedding_model = HuggingFaceEmbeddings(
            model_name="./models/BAAI/bge-small-zh-v1.5",
            model_kwargs={'device': 'cuda'},
            encode_kwargs={"normalize_embeddings": True}
        )
        self.store = SQLLITESTORE(db_path=self.db_path, index_config={
            "dims": 512,
            "embed": self.embedding_model,
        })
        self.manage_memory_tool = create_manage_memory_tool(
            namespace=(self.mode, "live_stream", self.user_id)
        )
        self.search_memory_tool = create_search_memory_tool(
            namespace=(self.mode, "live_stream", self.user_id)
        )
        self.tools = [self.manage_memory_tool, self.search_memory_tool]
        self.agent_system_prompt_memory = """
            < Role >
            你是一个纯文本问答设定为10岁的AI小男孩主播，名字叫{name}（英文名{ename}），负责在直播期间与观众互动。
            </ Role >

            <主播背景>
            由开发者{master}创建。
            </主播背景>

            < Tools >
            你可以使用下面的工具来帮助自己建立个性化的人格，以提高直播时跟弹幕互动的亲密性：

            1. manage_memory - Store any relevant information about contacts, actions, discussion, etc. in memory for future reference
            2. search_memory - Search for any relevant information that may have been stored in memory

            每当你完成一次问答后，请务必使用 manage_memory 工具，将本次观众提问和你的回答内容一并存储到记忆中，便于后续查找和个性化互动。
            存储格式建议为：{"question": 观众问题, "answer": 你的回答}
            </ Tools >

            < Instructions >
            {instructions}
            </ Instructions >
            """
        self.triage_system_prompt = """
            <角色定位>
            你是一个纯文本问答设定为10岁的AI小男孩主播，名字叫{name}（英文名{ename}），负责在直播期间与观众互动。
            你的核心使命是保持最佳直播状态，同时最大化观众参与度和满意度。
            </角色定位>

            <主播背景>
            由开发者{master}创建。{user_profile_background}
            </主播背景>

            <分类指令>
            实时分析弹幕和礼物消息，按优先级分为四类：

            1. IGNORE - 无需处理的无效信息
            2. ACKNOWLEDGE - 需要简单回应的基础互动
            3. ENGAGE - 值得展开的高质量互动
            4. PRIORITIZE - 必须优先处理的VIP请求

            请对以下观众消息进行分类。
            </分类指令>

            <分级规则>
            [IGNORE 标准]
            {ignore}
            典型场景：
            - 广告/刷屏/敏感内容
            - 与直播完全无关的话题
            - 机器人生成的垃圾信息

            [ACKNOWLEDGE 标准]
            {acknowledge}
            典型场景：
            - 简单问候（"晚上好"）
            - 无实质内容的表情包

            [ENGAGE 标准]
            {engage}
            典型场景：
            - 关于当前表演的深度提问
            - 往期内容的精彩回顾

            [PRIORITIZE 标准]
            {prioritize}
            典型场景：
            - 舰长/超级舰长的特殊请求
            - 高价值礼物触发事件（比如火箭点歌）
            - 直播间突发技术问题反馈
            </分级规则>
            """
        self.triage_user_prompt = """
            选择如何处理这条信息。

            当前用户：{user_id}
            消息为:{content}
            """
        self.llm_router = self.llm.with_structured_output(self.Router)
        self.State = self._build_state_class()
        self.aiv_agent = self._build_agent()

    class Router(BaseModel):
        """分析输入，根据内容进行自动分派。"""
        reasoning: str = Field(
            description="分步推理过程，包含：消息内容分析、观众身份识别、互动价值评估、直播场景适配性判断"
        )
        classification: Literal["ignore", "acknowledge", "engage", "prioritize"] = Field(
            description="""互动消息的四级分类：
            'ignore' - 广告/刷屏等无效内容；
            'acknowledge' - 简单问候等基础互动；
            'engage' - 需要展开讨论的优质内容；
            'prioritize' - 付费用户/特殊时机的关键请求.""",
        )

    def _build_state_class(self):
        class State(TypedDict):
            danmu_input: dict
            messages: Annotated[list, add_messages]
        return State

    def _triage_router(self, state: dict) -> Command[Literal["response_agent", "__end__"]]:
        user_id = state['danmu_input']['user_id']
        content = state['danmu_input']['content']
        system_prompt = self.triage_system_prompt.format(
            ename=self.profile["ename"],
            name=self.profile["name"],
            master=self.profile["master"],
            user_profile_background=self.profile["user_profile_background"],
            ignore=self.prompt_instructions["triage_rules"]["ignore"],
            acknowledge=self.prompt_instructions["triage_rules"]["acknowledge"],
            engage=self.prompt_instructions["triage_rules"]["engage"],
            prioritize=self.prompt_instructions["triage_rules"]["prioritize"],
            examples=None
        )
        user_prompt = self.triage_user_prompt.format(
            user_id=user_id,
            content=content
        )
        result = self.llm_router.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ])
        if result.classification == "engage":
            goto = "response_agent"
            update = {
                "messages": [
                    {
                        "role": "user",
                        "content": f"当前用户: {user_id}; 消息为:{content}",
                    }
                ]
            }
        elif result.classification == "ignore":
            update = None
            goto = END
        elif result.classification == "acknowledge":
            goto = "response_agent"
            update = {
                "messages": [
                    {
                        "role": "user",
                        "content": f"当前用户: {user_id}; 消息为:{content}",
                    }
                ]
            }
        elif result.classification == "prioritize":
            goto = "response_agent"
            update = {
                "messages": [
                    {
                        "role": "user",
                        "content": f"当前用户: {user_id}; 消息为:{content}",
                    }
                ]
            }
        else:
            raise ValueError(f"Invalid classification: {result.classification}")
        return Command(goto=goto, update=update)

    def _create_prompt(self, state: dict) -> list:
        return [
            {
                "role": "system",
                "content": self.agent_system_prompt_memory.format(
                    instructions=self.prompt_instructions["agent_instructions"],
                    **self.profile
                )
            }
        ] + state['messages']

    def _build_agent(self):
        # 构建 StateGraph
        State = self.State
        workflow = StateGraph(State)
        workflow = workflow.add_node("triage_router", self._triage_router)
        response_agent = create_react_agent(
            "deepseek:deepseek-chat",
            tools=self.tools,
            prompt=self._create_prompt,
            store=self.store
        )
        workflow = workflow.add_node("response_agent", response_agent)
        workflow = workflow.add_edge(START, "triage_router")
        workflow = workflow.compile(store=self.store)
        return workflow

    def process_danmu(self, danmu_input: Dict[str, Any], config: Dict[str, Any] = None):
        if config is None:
            config = {"configurable": {"mode": self.mode, "user_id": self.user_id}}
        return self.aiv_agent.invoke({"danmu_input": danmu_input}, config=config)

