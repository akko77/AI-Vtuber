import os

os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

from typing import  List, Sequence, Annotated, Literal
from typing_extensions import TypedDict
from langchain.tools import Tool, tool
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
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def escape_quotes(text):
  """Escapes both single and double quotes in a string.

  Args:
    text: The string to escape.

  Returns:
    The string with single and double quotes escaped.
  """
  return text.replace('"', '\\"').replace("'", "\\'")

# 初始化组件
logger.info("正在初始化嵌入模型...")
embedding_model = HuggingFaceEmbeddings(
    model_name="BAAI/bge-small-zh-v1.5",  # 推荐中文模型
    encode_kwargs={"normalize_embeddings": True}  # 余弦相似度需归一化
    )

logger.info("正在加载向量数据库...")
vectordb = FAISS.load_local("vector_db", embeddings=embedding_model, allow_dangerous_deserialization=True)
logger.info("向量数据库加载成功")

retriever = vectordb.as_retriever(
    search_type="similarity",  # 或 "mmr" (最大边际相关性)
    search_kwargs={
        "k": 10,  # 返回文档数量
    }
    )


# 提取所有原始文本
texts = [doc.page_content for doc in vectordb.docstore._dict.values()]
# 创建BM25检索器
bm25_retriever = BM25Retriever.from_texts(
    texts,
    metadatas=[doc.metadata for doc in vectordb.docstore._dict.values()]  # 保留元数据（可选）
    )
bm25_retriever.k = 10  # 返回结果数

# 组合检索器
hybrid_retriever = EnsembleRetriever(
    retrievers=[bm25_retriever, retriever],
    weights=[0.4, 0.6]  # 调节权重
)

from vector_search import BCEReranker

compressor = BCEReranker(
    model_name = "maidalun1020/bce-reranker-base_v1",
    top_n = 10,
    cache_folder="models",
    use_fp16=True
)
compression_retriever = ContextualCompressionRetriever(
    base_compressor=compressor,
    base_retriever=hybrid_retriever
)

retriever_tool = create_retriever_tool(
    compression_retriever,
    "retrieve_xiyouji",
    "用于搜索《西游记》白话文内容的工具，输入问题或关键词，返回相关段落。如果没有提到关键词'西游记'、'孙悟空'就不触发该检索。",

)

tools = [retriever_tool]

model = ChatOpenAI(
    base_url="https://api.deepseek.com/v1",
    model="deepseek-chat",
    streaming=True
)

question = f"""
根据你的知识库，回答以下问题。
请只回答问题，回答应该简洁且与问题相关。
如果你无法找到信息，不要放弃，尝试使用不同的参数再次调用你的 retriever 工具。
确保通过多次使用语义不同的查询来完全覆盖问题。
你的查询不应是问题，而是肯定形式的句子：例如，与其问"如何从 Hub 加载 bf16 模型？"，不如问"从 Hub 加载 bf16 权重"。

Question:
论语是孔子写的吗？
"""

first_answer = model.invoke(
        f"根据以下上下文回答问题:\n\n问题:{question}\n答案:"
        ).content

# 定义状态
class AgentState(TypedDict):
    # The add_messages function defines how an update should be processed
    # Default is to replace. add_messages says "append"
    question:str
    context: str
    answer: str
    messages: Annotated[Sequence[BaseMessage], add_messages]

### Edges

def keep_only_relevant_content(state):
    """
    Keeps only the relevant content from the retrieved documents that is relevant to the query.

    Returns:
        The relevant content from the retrieved documents that is relevant to the query.
    """


    keep_only_relevant_content_prompt_template = """you receive a query: {query} and retrieved documents: {retrieved_documents} from a
        vector store.
        You need to filter out all the non relevant information that don't supply important information regarding the {query}.
        your goal is just to filter out the non relevant information.
        you can remove parts of sentences that are not relevant to the query or remove whole sentences that are not relevant to the query.
        DO NOT ADD ANY NEW INFORMATION THAT IS NOT IN THE RETRIEVED DOCUMENTS.
        DO NOT ADD ANY NEW INFORMATION THAT IS NOT IN THE RETRIEVED DOCUMENTS.
        DO NOT ADD ANY NEW INFORMATION THAT IS NOT IN THE RETRIEVED DOCUMENTS.
        output the filtered relevant content.
        """
    prompt = PromptTemplate(
        template=keep_only_relevant_content_prompt_template,
        input_variables=["context", "question"],
    )
    model = ChatOpenAI(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        streaming=True
    )

    messages = state["messages"]
    last_message = messages[-1]

    # question = messages[0].content
    context = last_message.content
    question = state["question"]
    context_add = state["context"]
    context = context + "\n" + context_add
    
    print("retrieved_documents...")
    print("--------------------")
    print("context: ", context)

    input_data = {
        "query": question,
        "retrieved_documents": context
    }

    chain = prompt | model | StrOutputParser()

    print("keeping only the relevant content...")
    print("--------------------")
    output = chain.invoke(input_data)
    relevant_content = "".join(output)

    print("relevant_content", relevant_content)

    return {"messages": state["messages"],"question":question,"context":relevant_content}


def grade_documents(state) -> Literal["generate", "rewrite"]:
    """
    Determines whether the retrieved documents are relevant to the question.

    Args:
        state (messages): The current state

    Returns:
        str: A decision for whether the documents are relevant or not
    """

    print("---CHECK RELEVANCE---")

    # Data model
    class grade(BaseModel):
        """Binary score for relevance check."""

        binary_score: str = Field(description="Relevance score 'yes' or 'no'")

    model = ChatOpenAI(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        streaming=True
    )
    # LLM with tool and validation
    llm_with_tool = model.with_structured_output(grade)

    # Prompt
    prompt = PromptTemplate(
        template="""You are a grader assessing relevance of a retrieved document to a user question. \n 
        Here is the retrieved document: \n\n {context} \n\n
        Here is the user question: {question} \n
        If the document contains keyword(s) or semantic meaning related to the user question, grade it as relevant. \n
        Give a binary score 'yes' or 'no' score to indicate whether the document is relevant to the question.""",
        input_variables=["context", "question"],
    )

    # Chain
    chain = prompt | model | StrOutputParser()

    messages = state["messages"]
    last_message = messages[-1]

    # question = messages[0].content
    # docs = last_message.content
    question = state["question"]
    docs = state["context"]

    print("docs: ",docs)

    scored_result = chain.invoke({"question": question, "context": docs})

    # score = scored_result.binary_score

    if scored_result == "yes":
        print("---DECISION: DOCS RELEVANT---")
        print(scored_result)
        return "generate"

    else:
        print("---DECISION: DOCS NOT RELEVANT---")
        print(scored_result)
        # return "generate"
        return "rewrite"


### Nodes


def agent(state):
    """
    Invokes the agent model to generate a response based on the current state. Given
    the question, it will decide to retrieve using the retriever tool, or simply end.

    Args:
        state (messages): The current state

    Returns:
        dict: The updated state with the agent response appended to messages
    """
    print("---CALL AGENT---")
    messages = state["messages"]
    question = [
        HumanMessage(
            content=state["question"]
        )
    ]
    
    model = ChatOpenAI(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        streaming=True
    )
    model = model.bind_tools(tools)
    # response = model.invoke(messages)
    response = model.invoke(question)
    # We return a list, because this will get added to the existing list
    return {"messages": [response]}


def rewrite(state):
    """
    Transform the query to produce a better question.

    Args:
        state (messages): The current state

    Returns:
        dict: The updated state with re-phrased question
    """

    print("---TRANSFORM QUERY---")
    messages = state["messages"]
    question = state["question"]

    msg = [
        HumanMessage(
            content=f""" \n 
                Look at the input and try to reason about the underlying semantic intent / meaning. \n 
                Here is the initial question:
                \n ------- \n
                {question} 
                \n ------- \n
                Formulate an improved question: """,
        )
    ]


    model = ChatOpenAI(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        streaming=True
    )

    print("msg: ", msg )
    # Grader
    response = model.invoke(msg)
    return {"messages": [response],"question":response.content}


def generate(state):
    """
    Generate answer

    Args:
        state (messages): The current state

    Returns:
         dict: The updated state with re-phrased question
    """
    print("---GENERATE---")
    messages = state["messages"]
    # question = messages[0].content
    last_message = messages[-1]

    docs = last_message.content
    question = state["question"]
    # context = state["context"]


    # Prompt
    prompt = hub.pull("rlm/rag-prompt")


    # Post-processing
    def format_docs(docs):
        return "\n\n".join(doc.page_content for doc in docs)

    model = ChatOpenAI(
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",
        streaming=True
    )
    # Chain
    rag_chain = prompt | model | StrOutputParser()

    # Run
    response = rag_chain.invoke({"context": docs, "question": question})
    return {"answer": response}


print("*" * 20 + "Prompt[rlm/rag-prompt]" + "*" * 20)
prompt = hub.pull("rlm/rag-prompt").pretty_print()  # Show what the prompt looks like



# 定义工具
@tool
def retriever(query: str) -> str:
    """执行向量数据库相似性搜索"""
    logger.info(f"正在查询: {query}")
    results = vectordb.similarity_search(query, k=5)
    logger.info("查询完成")
    return "\n\n".join([f"资料{i+1}: {result.page_content}" for i, result in enumerate(results)])



# 添加节点
def generate_queries(state: AgentState):
    """生成检索查询"""
    question = state["question"]
    # 这里可以添加更智能的查询生成逻辑
    queries = [
        question,
        f"关于{question}的详细信息",
        f"{question}的相关背景"
    ]
    return {"retrieval_queries": queries}

def retrieve_docs(state: AgentState):
    """执行检索"""
    retrieved = []
    for query in state["retrieval_queries"]:
        retrieved.append(retriever.run(query))
    return {"retrieved_docs": retrieved}

def generate_answer(state: AgentState):
    """生成最终答案"""
    context = "\n\n".join(state["retrieved_docs"])
    response = model.invoke(
        f"根据以下上下文回答问题:\n{context}\n\n问题:{state['question']}\n答案:"
    )
    return {"final_answer": response.content}


# 创建图
workflow = StateGraph(AgentState)
if 0==1:
    # 添加节点到图
    workflow.add_node("generate_queries", generate_queries)
    workflow.add_node("retrieve_docs", retrieve_docs)
    workflow.add_node("generate_answer", generate_answer)

    # 定义边
    workflow.add_edge("generate_queries", "retrieve_docs")
    workflow.add_edge("retrieve_docs", "generate_answer")
    workflow.add_edge("generate_answer", END)

    # 设置入口点
    workflow.set_entry_point("generate_queries")
else:
    # Define the nodes we will cycle between
    workflow.add_node("agent", agent)  # agent
    retrieve = ToolNode([retriever_tool])
    workflow.add_node("retrieve", retrieve)  # retrieval
    workflow.add_node("rewrite", rewrite)  # Re-writing the question
    workflow.add_node(
        "generate", generate
    )  # Generating a response after we know the documents are relevant
    # Call agent node to decide to retrieve or not
    workflow.add_node("keep_only_relevant_content",keep_only_relevant_content)
    workflow.add_edge(START, "agent")

    # Decide whether to retrieve
    workflow.add_conditional_edges(
        "agent",
        # Assess agent decision
        tools_condition,
        {
            # Translate the condition outputs to nodes in our graph
            "tools": "retrieve",
            # END: END,
            END: generate,
        },
    )

    workflow.add_edge("retrieve", "keep_only_relevant_content")
    # Edges taken after the `action` node is called.
    workflow.add_conditional_edges(
        # "retrieve",
        "keep_only_relevant_content",
        # Assess agent decision
        grade_documents,
    )
    workflow.add_edge("generate", END)
    workflow.add_edge("rewrite", "agent")

# 编译图
graph = workflow.compile()

# 运行

# result = app.invoke({
#     "question": question,
#     "retrieval_queries": [],
#     "retrieved_docs": [],
#     "final_answer": ""
# })

# context = "悟空忽然露出一副凶相，扔掉瓷钵，拿出铁棒，对着唐僧背后就是一下。唐僧立刻昏倒在地上。悟空把两个包袱提在手中，驾起筋斗云，立刻无影无踪。"
context = ""
inputs = {
    "question":question,
    "context":context,
    "messages": [
        ("user", question),
    ]
}

print("最初答案:", first_answer)
print("最终答案:")
for output in graph.stream(inputs):
    for key, value in output.items():
        print("\n")
        print(f"Output from node '{key}':")
        print("---")
        print(value)
    print("\n---\n")
# print("最终答案:", result["final_answer"])