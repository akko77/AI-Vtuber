# xiyouji_qa_system.py
import os
os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
from typing import List, Sequence, Annotated, Literal, Dict, Any
from typing_extensions import TypedDict
from langchain.tools import Tool
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_openai.chat_models import ChatOpenAI
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode, tools_condition
from langchain.tools.retriever import create_retriever_tool
from langchain.retrievers import BM25Retriever, EnsembleRetriever, ContextualCompressionRetriever
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.graph.message import add_messages
from langchain_core.prompts import PromptTemplate
from langchain import hub
from langchain_core.output_parsers import StrOutputParser
from pydantic import BaseModel, Field
import logging
from utils.my_log import logger
from dotenv import load_dotenv

from langchain.embeddings import HuggingFaceBgeEmbeddings

# from rag.usermemory import UserMemory
from rag.memory_stores.sqlite_store import SQLLITESTORE
from langmem import create_manage_memory_tool, create_search_memory_tool

class AgenticRAG:
    """
    西游记问答系统，封装为可调用的类
    
    使用方法:
    from rag import AgenticRAG
    
    qa = AgenticRAG()
    answer = qa.invoke("孙悟空的金箍棒有多重?")
    print(answer)
    """
    
    def __init__(self, user_id:str = "default" ,vector_db_path: str = "rag/vector_db"):
        """
        初始化问答系统
        
        Args:
            vector_db_path: 向量数据库路径，默认为"vector_db"
        """
        # 配置环境
        os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
        load_dotenv()
        self.db_path = "memory_Chico.sqlite" # 记忆库
        self.user_id = user_id
        self.mode = "test"

        # 初始化日志
        logging.basicConfig(level=logging.INFO)
        # self.logger = logging.getLogger(__name__)
        self.logger = logger
        
        # 初始化模型和数据库
        self._initialize_components(vector_db_path)
        
        # 构建问答图
        self._build_workflow()
    
    def _initialize_components(self, vector_db_path: str):
        """初始化所有组件"""
        self.logger.info("正在初始化嵌入模型...")
        os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
        load_dotenv()
        print(os.environ.get('HF_ENDPOINT'))  # 检查是否生效
        models_dir = os.listdir('rag/models')  # 如果目录存在
        print("models 目录内容:", models_dir)
        # self.embedding_model = HuggingFaceEmbeddings(
        self.embedding_model = HuggingFaceBgeEmbeddings(
            
            model_name="rag/models/BAAI/bge-small-zh-v1.5",
            # cache_folder="rag/models",
            # model_kwargs = {'device': 'cuda'},
            encode_kwargs={"normalize_embeddings": True}
        )
        
        self.logger.info("正在加载向量数据库...")
        self.vectordb = FAISS.load_local(
            vector_db_path, 
            embeddings=self.embedding_model, 
            allow_dangerous_deserialization=True
        )
        self.logger.info("向量数据库加载成功")
        
        # 初始化检索器
        self._initialize_retrievers()
        
        

        # 初始化语言模型
        self.model = ChatOpenAI(
            base_url="https://api.deepseek.com/v1",
            model="deepseek-chat",
            streaming=True
        )
    
    def _initialize_retrievers(self):
        """初始化各种检索器"""
        # 向量检索器
        self.retriever = self.vectordb.as_retriever(
            search_type="similarity",
            search_kwargs={"k": 10}
        )
        
        # BM25检索器
        texts = [doc.page_content for doc in self.vectordb.docstore._dict.values()]
        self.bm25_retriever = BM25Retriever.from_texts(
            texts,
            metadatas=[doc.metadata for doc in self.vectordb.docstore._dict.values()]
        )
        self.bm25_retriever.k = 10
        
        # 混合检索器
        self.hybrid_retriever = EnsembleRetriever(
            retrievers=[self.bm25_retriever, self.retriever],
            weights=[0.4, 0.6]
        )
        
        # 重排序器
        from rag.vector_search import BCEReranker
        self.compressor = BCEReranker(
            model_name="rag/models/maidalun1020/bce-reranker-base_v1",
            top_n=10,
            cache_folder="models",
            use_fp16=True
        )
        
        self.compression_retriever = ContextualCompressionRetriever(
            base_compressor=self.compressor,
            base_retriever=self.hybrid_retriever
        )
        
        # 检索工具
        self.retriever_tool = create_retriever_tool(
            self.compression_retriever,
            "retrieve_xiyouji",
            "用于搜索《西游记》白话文内容的工具，输入问题或关键词，返回相关段落。如果没有提到关键词'西游记'、'孙悟空'就不触发该检索。",
        )
        
        # 初始化记忆库
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


        self.tools = [self.retriever_tool, self.manage_memory_tool, self.search_memory_tool]
    
    def _build_workflow(self):
        """构建问答工作流图"""
        # 定义状态
        class AgentState(TypedDict):
            question: str
            context: str
            instruct: str
            answer: str
            messages: Annotated[Sequence[BaseMessage], add_messages]
        
        # 创建工作流
        self.workflow = StateGraph(AgentState)
        
        # 添加节点
        self.workflow.add_node("agent", self._agent_node)
        # retrieve = ToolNode([self.retriever_tool])
        retrieve = ToolNode(self.tools)
        self.workflow.add_node("retrieve", retrieve)
        self.workflow.add_node("rewrite", self._rewrite_node)
        self.workflow.add_node("generate", self._generate_node)
        self.workflow.add_node("keep_only_relevant_content", self._keep_only_relevant_content_node)
        
        # 添加边
        self.workflow.add_edge(START, "agent")
        self.workflow.add_conditional_edges(
            "agent",
            tools_condition,
            {
                "tools": "retrieve",
                # END: END,
                END: "generate",
            },
        )
        self.workflow.add_edge("retrieve", "keep_only_relevant_content")
        self.workflow.add_conditional_edges(
            "keep_only_relevant_content",
            self._grade_documents,
        )
        self.workflow.add_edge("generate", END)
        self.workflow.add_edge("rewrite", "agent")
        
        # 编译图
        self.graph = self.workflow.compile(store=self.store)
    
    def invoke(self, input):
        """
        提问并获取答案
        
        Args:
            input: 要提问的问题
            
        Returns:
            系统生成的答案
        """
        # username = input[3]
        
        # if username == "Will":
        #     instruct += "\n当前用户是：" + username
       
        context = "".join("{" + i["role"] + ":" + i["content"] + "}" for i in input[1:-1])
        # for i in input[1:]:
        #     print("Current item type:", type(i))  # 检查每个元素的类型
        #     print("Item content:", i)

        mem_prompt = """
            < Tools >
            你可以使用下面的工具来帮助自己建立个性化的人格，以提高直播时跟弹幕互动的亲密性,但是不要记录retriever_tool中检索到的内容：

            1. manage_memory - Store any relevant information about contacts, actions, discussion, etc. in memory for future reference
            2. search_memory - Search for any relevant information that may have been stored in memory

            每当你完成一次问答后，请务必使用 manage_memory 工具，将本次观众提问和你的回答内容一并存储到记忆中，便于后续查找和个性化互动。
            存储格式建议为：{"question": 观众问题, "answer": 你的回答}
            </ Tools >
            """
        instruct = mem_prompt + "后面的是用户问题的背景：" + input[0]["content"] + input[1]["content"] + "\n历史会话：" + context

        inputs = {
            "question":input[-1]["content"],
            "instruct":instruct,
            "context":input[1]["content"],
            "messages": [
                ("user", input[-1]["content"]),
            ]
        }
        
        # 运行完整的工作流
        
        for output in self.graph.stream(inputs):
            for key, value in output.items():
                self.logger.info(f"Output from node '{key}':")
                self.logger.info(f"{value}':")
                
                if key == 'generate':
                    final_answer = value["answer"]
                    self.logger.info(f"最终答案: {final_answer}")
        
        return final_answer
    
    # 以下是工作流节点函数
    def _agent_node(self, state):
        """代理节点"""
        self.logger.info("---CALL AGENT---")
        question = state["question"]
        context = state["context"]
        instruct = state["instruct"]
        question = [HumanMessage(
            content=f"""
                system:{context}\n
                instruct:{instruct}\n
                question:{question}
                                 
            """)]

        # question = state["question"]
        print("Messages being sent:", question)

        model = self.model.bind_tools(self.tools)
        
        response = model.invoke(question)
        # response = model.stream(question)
        return {"messages": [response],"answer":response}
    
    def _rewrite_node(self, state):
        """重写问题节点"""
        self.logger.info("---TRANSFORM QUERY---")
        question = state["question"]
        
        msg = [HumanMessage(
            content=f"""\nLook at the input and try to reason about the underlying semantic intent / meaning. \n 
            DO NOT ADD ANY NEW INFORMATION THAT IS NOT RELEVENT TO THE QUESTION.\n
            Here is the initial question:\n-------\n{question}\n-------\n
            Formulate an improved question: """
        )]
        
        response = self.model.invoke(msg)
        return {"messages": [response], "question": response.content}
    
    def _generate_node(self, state):
        """生成答案节点"""
        self.logger.info("---GENERATE---")
        messages = state["messages"]
        last_message = messages[-1]
        docs = last_message.content
        question = state["question"]
        instruct = state["instruct"]
        
        
        #You are an assistant for question-answering tasks. 
        input = [
            {"role":"system","content":instruct},
            {"role":"system",
             "content":f"""
                Use the following pieces of retrieved context to answer the question. 
                If you don't know the answer, just say that you don't know. 
                请简要回答问题，且一定要在中国人的设定下。\n
                context:{docs}
                """},
            {"role":"user","content":question},
        ]
        print("最终提问:", input)
        import openai
        client = openai.OpenAI(base_url="https://api.deepseek.com/v1")
                # 调用 ChatGPT 接口生成回复消息
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=input,
            timeout=30,
            stream=True,
        )

        # prompt = hub.pull("rlm/rag-prompt")
        # rag_chain = prompt | self.model | StrOutputParser()
        # response = rag_chain.invoke({"context": docs, "question": question})

        # rag_chain = prompt | self.model 
        # response = rag_chain.stream({"context": docs, "question": question})

        
        return {"answer": response}
    
    def _keep_only_relevant_content_node(self, state):
        """保留相关内容节点"""
        prompt_template = """you receive a query: {query} and retrieved documents: {retrieved_documents} from a
            vector store. You need to filter out all the non relevant information that don't supply important 
            information regarding the {query}. your goal is just to filter out the non relevant information.
            you can remove parts of sentences that are not relevant to the query or remove whole sentences 
            that are not relevant to the query. DO NOT ADD ANY NEW INFORMATION THAT IS NOT IN THE RETRIEVED DOCUMENTS.
            output the filtered relevant content."""
        
        prompt = PromptTemplate(
            template=prompt_template,
            input_variables=["context", "question"],
        )
        
        messages = state["messages"]
        last_message = messages[-1]
        question = state["question"]
        # context_add = state["context"]
        # context = last_message.content + "\n" + context_add
        context = last_message.content
        
        input_data = {"query": question, "retrieved_documents": context}
        chain = prompt | self.model | StrOutputParser()
        output = chain.invoke(input_data)
        relevant_content = "".join(output)
        
        return {"messages": state["messages"], "question": question, "context": relevant_content}
    
    def _grade_documents(self, state) -> Literal["generate", "rewrite"]:
        """评估文档相关性"""
        self.logger.info("---CHECK RELEVANCE---")
        
        class grade(BaseModel):
            binary_score: str = Field(description="Relevance score 'yes' or 'no'")
        
        prompt = PromptTemplate(
            template="""You are a grader assessing relevance of a retrieved document to a user question. \n 
            Here is the retrieved document: \n\n {context} \n\n
            Here is the user question: {question} \n
            If the document contains keyword(s) or semantic meaning related to the user question, grade it as relevant. \n
            Give a binary score 'yes' or 'no' score to indicate whether the document is relevant to the question.""",
            input_variables=["context", "question"],
        )
        
        chain = prompt | self.model | StrOutputParser()
        question = state["question"]
        docs = state["context"]
        
        scored_result = chain.invoke({"question": question, "context": docs})
        
        if scored_result == "yes":
            self.logger.info("---DECISION: DOCS RELEVANT---")
            return "generate"
        else:
            self.logger.info("---DECISION: DOCS NOT RELEVANT---")
            return "rewrite"