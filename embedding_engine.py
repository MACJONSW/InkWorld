"""
神经向量嵌入引擎 - 基于 Embedding API 的语义检索
支持 OpenAI 兼容 embedding 接口，无模型时降级到 TF-IDF
"""
import uuid
import json
import logging
import numpy as np
from datetime import datetime
from openai import OpenAI
import httpx

try:
    import faiss
    HAS_FAISS = True
except ImportError:
    HAS_FAISS = False

logger = logging.getLogger(__name__)


class EmbeddingEngine:
    def __init__(self, db):
        self.db = db
        self._indices = {}      # book_id -> faiss.IndexFlatIP or np.array
        self._chunks_cache = {} # book_id -> list of chunk dicts
        self._dims = {}         # book_id -> int

    # ------------------------------------------------------------------
    #  Client helpers
    # ------------------------------------------------------------------

    def get_embedding_client(self, user_id):
        """从用户模型配置中查找 role='embedding' 的模型，返回 (client, model_id, config) 或 (None, None, None)"""
        if not user_id:
            return None, None, None
        model_config = self.db.get_model_for_role('embedding', user_id)
        if not model_config:
            return None, None, None
        client = OpenAI(
            api_key=model_config['api_key'],
            base_url=model_config['base_url'],
            timeout=120.0,
            http_client=httpx.Client(timeout=120.0, follow_redirects=True)
        )
        return client, model_config['model_id'], model_config

    # ------------------------------------------------------------------
    #  Embed
    # ------------------------------------------------------------------

    def embed_texts(self, user_id, texts, batch_size=32):
        """调用 embedding API 批量生成向量，返回 np.array [N, dim]"""
        client, model_id, _ = self.get_embedding_client(user_id)
        if not client or not texts:
            return np.array([])

        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            # 截断过长文本
            batch = [t[:8000] if len(t) > 8000 else t for t in batch]
            try:
                resp = client.embeddings.create(model=model_id, input=batch)
                for item in resp.data:
                    all_embeddings.append(item.embedding)
            except Exception as e:
                logger.error("Embedding API error: %s", e)
                # 填 0 向量占位
                dim = len(all_embeddings[0]) if all_embeddings else 1536
                for _ in batch:
                    all_embeddings.append([0.0] * dim)

        return np.array(all_embeddings, dtype='float32') if all_embeddings else np.array([])

    # ------------------------------------------------------------------
    #  Index build
    # ------------------------------------------------------------------

    def _collect_chunks(self, book_id, chunk_size=500, overlap=100):
        """收集全书文本块"""
        chunks = []

        def _split(text, source, source_id, meta=None):
            if not text or not text.strip():
                return
            meta = meta or {}
            for ci in range(0, len(text), chunk_size - overlap):
                segment = text[ci:ci + chunk_size]
                if len(segment.strip()) < 20:
                    continue
                chunks.append({
                    'text': segment,
                    'source_type': source,
                    'source_id': source_id,
                    'chunk_index': ci // (chunk_size - overlap),
                    'metadata': meta
                })

        # Lorebook
        for entry in self.db.get_lorebook_entries(book_id):
            if not entry.get('enabled', 1):
                continue
            text = f"{entry.get('name', '')}: {entry.get('description', '')}\n{entry.get('content', '')}"
            _split(text, 'lorebook', entry['id'], {'name': entry.get('name', '')})

        # Summaries
        for summary in self.db.get_chapter_summaries(book_id):
            text = '\n'.join([part for part in [summary.get('summary', ''), summary.get('key_events', '')] if part])
            _split(text, 'summary', summary['id'], {'chapter_title': summary.get('chapter_title', '')})

        # Character history
        for history in self.db.get_character_history(book_id):
            text = f"{history.get('character_name', '')}: {history.get('summary', '')} {history.get('details', '')}"
            _split(text, 'character_history', history['id'], {'character_name': history.get('character_name', '')})

        # World state
        for state in self.db.get_world_state(book_id):
            text = f"{state.get('entity_name', '')}: {state.get('state_type', '')}={state.get('state_value', '')}"
            _split(text, 'world_state', state['id'], {'entity_name': state.get('entity_name', '')})

        # Foreshadowing
        for item in self.db.get_foreshadowing(book_id):
            text = f"{item.get('label', '')}: {item.get('text', '')} {item.get('description', '')} {item.get('resolved_text', '')}"
            _split(text, 'foreshadowing', item['id'], {'label': item.get('label', '')})

        # Node content
        for node in self.db.get_all_node_contents(book_id):
            if node.get('type') not in ('chapter', 'scene'):
                continue
            _split(node.get('content', ''), 'content', node['id'], {'chapter_title': node.get('title', '')})

        return chunks

    def build_index(self, book_id, user_id):
        """为全书构建 embedding 索引"""
        chunks = self._collect_chunks(book_id)
        if not chunks:
            return {'status': 'empty', 'chunk_count': 0, 'dim': 0}

        texts = [c['text'] for c in chunks]
        vectors = self.embed_texts(user_id, texts)
        if vectors.size == 0:
            return {'status': 'error', 'message': 'Embedding API 未返回结果'}

        dim = vectors.shape[1]

        # 存入数据库
        now = datetime.now().isoformat()
        self.db.delete_embedding_chunks(book_id)
        for i, chunk in enumerate(chunks):
            chunk['id'] = str(uuid.uuid4())[:12]
            chunk['book_id'] = book_id
            chunk['chunk_text'] = chunk['text']
            chunk['embedding'] = vectors[i].tobytes()
            chunk['created_at'] = now
        self.db.save_embedding_chunks(chunks)
        client_, model_id, _ = self.get_embedding_client(user_id)
        self.db.save_embedding_index_meta(book_id, user_id, dim, len(chunks), model_id or 'unknown', now)

        # 内存缓存
        self._build_memory_index(book_id, chunks, vectors, dim)

        return {'status': 'ok', 'chunk_count': len(chunks), 'dim': dim}

    def _build_memory_index(self, book_id, chunks, vectors, dim):
        """在内存中构建 FAISS 索引"""
        self._chunks_cache[book_id] = chunks
        self._dims[book_id] = dim
        if HAS_FAISS:
            index = faiss.IndexFlatIP(dim)
            vecs = vectors.copy().astype('float32')
            faiss.normalize_L2(vecs)
            index.add(vecs)
            self._indices[book_id] = index
        else:
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            norms[norms == 0] = 1
            self._indices[book_id] = vectors / norms

    def incremental_update(self, book_id, user_id, source_type, source_id):
        """增量更新单个来源的向量（先删旧的，再加新的）"""
        self.db.delete_embedding_chunks_by_source(book_id, source_type, source_id)
        # 重新收集该来源的 chunks
        all_chunks = self._collect_chunks(book_id)
        relevant = [c for c in all_chunks if c['source_type'] == source_type and c['source_id'] == source_id]
        if not relevant:
            meta = self.db.get_embedding_index_meta(book_id) or {}
            client_, model_id, _ = self.get_embedding_client(user_id)
            self.db.save_embedding_index_meta(
                book_id,
                user_id,
                meta.get('dim', 0),
                len(self.db.get_embedding_chunks(book_id)),
                model_id or meta.get('model_id', 'unknown'),
                datetime.now().isoformat(),
            )
            self._invalidate_cache(book_id)
            return 0

        texts = [c['text'] for c in relevant]
        vectors = self.embed_texts(user_id, texts)
        if vectors.size == 0:
            return 0

        now = datetime.now().isoformat()
        for i, chunk in enumerate(relevant):
            chunk['id'] = str(uuid.uuid4())[:12]
            chunk['book_id'] = book_id
            chunk['chunk_text'] = chunk['text']
            chunk['embedding'] = vectors[i].tobytes()
            chunk['created_at'] = now
        self.db.save_embedding_chunks(relevant)

        meta = self.db.get_embedding_index_meta(book_id) or {}
        client_, model_id, _ = self.get_embedding_client(user_id)
        self.db.save_embedding_index_meta(
            book_id,
            user_id,
            vectors.shape[1],
            len(self.db.get_embedding_chunks(book_id)),
            model_id or meta.get('model_id', 'unknown'),
            now,
        )

        # 重建内存索引
        self._invalidate_cache(book_id)
        return len(relevant)

    # ------------------------------------------------------------------
    #  Retrieve
    # ------------------------------------------------------------------

    def _ensure_loaded(self, book_id, user_id=None):
        """确保内存中有索引"""
        if book_id in self._indices:
            return True
        # 从数据库加载
        meta = self.db.get_embedding_index_meta(book_id)
        if not meta:
            return False
        db_chunks = self.db.get_embedding_chunks(book_id)
        if not db_chunks:
            return False

        dim = meta['dim']
        vectors = []
        chunks = []
        for row in db_chunks:
            if row.get('embedding'):
                vec = np.frombuffer(row['embedding'], dtype='float32')
                if len(vec) == dim:
                    vectors.append(vec)
                    chunks.append(row)

        if not vectors:
            return False

        vecs_array = np.array(vectors, dtype='float32')
        self._build_memory_index(book_id, chunks, vecs_array, dim)
        return True

    def retrieve(self, book_id, user_id, query, top_k=5, source_filter=None):
        """语义检索"""
        if not self._ensure_loaded(book_id, user_id):
            return []

        # 生成 query 向量
        query_vec = self.embed_texts(user_id, [query])
        if query_vec.size == 0:
            return []

        chunks = self._chunks_cache.get(book_id, [])
        if not chunks:
            return []

        dim = self._dims.get(book_id, query_vec.shape[1])

        results = []
        if HAS_FAISS and isinstance(self._indices.get(book_id), faiss.Index):
            qv = query_vec.copy().astype('float32')
            faiss.normalize_L2(qv)
            n = min(top_k * 3, len(chunks))
            scores, indices = self._indices[book_id].search(qv, n)
            for score, idx in zip(scores[0], indices[0]):
                if 0 <= idx < len(chunks) and score > 0.01:
                    c = dict(chunks[idx])
                    c['score'] = round(float(score), 4)
                    c['retrieval_type'] = 'embedding'
                    results.append(c)
        else:
            doc_vecs = self._indices.get(book_id)
            if doc_vecs is None or not isinstance(doc_vecs, np.ndarray):
                return []
            qv = query_vec.copy().astype('float32')
            norms = np.linalg.norm(qv, axis=1, keepdims=True)
            norms[norms == 0] = 1
            qv = qv / norms
            sims = (doc_vecs @ qv.T).flatten()
            sorted_idx = np.argsort(sims)[::-1][:top_k * 3]
            for idx in sorted_idx:
                if sims[idx] > 0.05:
                    c = dict(chunks[idx])
                    c['score'] = round(float(sims[idx]), 4)
                    c['retrieval_type'] = 'embedding'
                    results.append(c)

        # 按 source_filter 过滤
        if source_filter:
            filters = source_filter if isinstance(source_filter, list) else [source_filter]
            results = [r for r in results if r.get('source_type') in filters]

        return results[:top_k]

    def has_index(self, book_id):
        """检查是否已构建 embedding 索引"""
        if book_id in self._indices:
            return True
        meta = self.db.get_embedding_index_meta(book_id)
        return meta is not None

    def get_status(self, book_id):
        """获取索引状态"""
        meta = self.db.get_embedding_index_meta(book_id)
        if not meta:
            return {'has_index': False}
        return {
            'has_index': True,
            'dim': meta['dim'],
            'chunk_count': meta['chunk_count'],
            'model_id': meta['model_id'],
            'last_built_at': meta['last_built_at']
        }

    def _invalidate_cache(self, book_id):
        """清除内存缓存，下次检索时重新加载"""
        self._indices.pop(book_id, None)
        self._chunks_cache.pop(book_id, None)
        self._dims.pop(book_id, None)
