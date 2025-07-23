import os

os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
import logging
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.retrievers import BM25Retriever, EnsembleRetriever, ContextualCompressionRetriever


from typing import List, Any, Optional
from langchain_core.documents import Document
from langchain.retrievers.document_compressors.base import BaseDocumentCompressor
from FlagEmbedding import FlagReranker
from pydantic import Field

class BCEReranker(BaseDocumentCompressor):
    """修正后的 BCE Reranker 包装器"""
    
    model_name: str = Field(default="maidalun1020/bce-reranker-base_v1")
    top_n: int = Field(default=3)
    use_fp16: bool = Field(default=False)
    cache_folder: Optional[str] = Field(default=None)
    
    # 需要显式声明模型字段
    model: Any = Field(default=None, exclude=True)  # exclude=True 表示不包含在序列化中
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # 初始化实际模型
        self.model = FlagReranker(
            model_name_or_path=self.model_name,
            use_fp16=self.use_fp16,
            cache_folder=self.cache_folder
        )
    
    def compress_documents(
        self,
        documents: List[Document],
        query: str,
        **kwargs: Any,
    ) -> List[Document]:
        if not documents:
            return []
            
        pairs = [[query, doc.page_content] for doc in documents]
        scores = self.model.compute_score(pairs)
        scored_docs = sorted(zip(scores, documents), key=lambda x: x[0], reverse=True)
        return [doc for _, doc in scored_docs[:self.top_n]]
    

def run():
    # 配置日志
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    # 1. 加载向量数据库
    logger.info("正在加载向量数据库...")
    embedding_model = HuggingFaceEmbeddings(
        model_name="BAAI/bge-small-zh-v1.5",  # 推荐中文模型
        encode_kwargs={"normalize_embeddings": True}  # 余弦相似度需归一化
    )
    vectordb = FAISS.load_local(
        "vector_db", 
        embeddings=embedding_model,
        allow_dangerous_deserialization=True
    )
    logger.info("向量数据库加载成功")

    # 2. 直接测试搜索
    def test_vector_search(query: str, k: int = 3):
        """测试向量搜索并打印结果和相似度"""
        # 获取带分数的结果
        docs_with_scores = vectordb.similarity_search_with_score(query, k=k)
        
        print(f"\n=== 测试查询: '{query}' ===")
        for i, (doc, score) in enumerate(docs_with_scores):
            print(f"\n结果 {i+1} | 相似度: {1 - score:.4f} (L2距离: {score:.4f})")
            print(f"内容: {doc.page_content[:200]}...")  # 只打印前200字符
            if doc.metadata:  # 打印元数据（如果有）
                print(f"元数据: {doc.metadata}")

    # 配置检索器
    retriever = vectordb.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 20}
    )

    # 提取所有原始文本
    texts = [doc.page_content for doc in vectordb.docstore._dict.values()]
    # 创建BM25检索器
    bm25_retriever = BM25Retriever.from_texts(
        texts,
        metadatas=[doc.metadata for doc in vectordb.docstore._dict.values()]  # 保留元数据（可选）
        )
    bm25_retriever.k = 20  # 返回结果数

    # 组合检索器
    hybrid_retriever = EnsembleRetriever(
        retrievers=[bm25_retriever, retriever],
        weights=[0.4, 0.6]  # 调节权重
    )

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

    import numpy as np
    # 测试检索器
    def test_retriever(query: str, verbose: bool):
        print(f"\n=== 检索器测试: '{query}' ===")
        docs = hybrid_retriever.invoke(query)
        
        compressed_docs = compression_retriever.get_relevant_documents(query)

        # 由于retriever不直接返回分数，需要单独计算
        query_embedding = embedding_model.embed_query(query)
        if verbose:
            for i, doc in enumerate(docs):
                doc_embedding = embedding_model.embed_query(doc.page_content)
                cosine_sim = np.dot(query_embedding, doc_embedding)  # 向量已归一化时成立
                print(f"\n结果 {i+1} | 预估余弦相似度: {cosine_sim:.4f}")
                print(f"内容: {doc.page_content}")

        for i, doc in enumerate(compressed_docs):
            doc_embedding = embedding_model.embed_query(doc.page_content)
            cosine_sim = np.dot(query_embedding, doc_embedding)  # 向量已归一化时成立
            print(f"\nrerank结果 {i+1} | 预估余弦相似度: {cosine_sim:.4f}")
            print(f"rerank内容: {doc.page_content}")


    question = f"""
    孙悟空的师傅
    """

    # 3. 运行测试
    test_queries = [
        question,
    ]

    for query in test_queries:
        test_retriever(query,verbose=False)

if __name__=="__main__":
    run()