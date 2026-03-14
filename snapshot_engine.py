"""
快照与回收站引擎
"""
import json


class SnapshotEngine:
    def __init__(self, db):
        self.db = db

    def create_node_snapshot(self, book_id, node_id, snapshot_type='auto_rewrite', label=''):
        """Create a snapshot of a specific node's content and versions."""
        content = self.db.get_node_content(node_id)
        versions = self.db.get_versions(node_id)
        snapshot_data = {
            'node_id': node_id,
            'content': content.get('content', ''),
            'word_count': content.get('word_count', 0),
            'versions': [{'id': v['id'], 'label': v['label'], 'content': v['content'],
                          'is_active': v['is_active']} for v in versions]
        }
        return self.db.create_snapshot({
            'book_id': book_id,
            'node_id': node_id,
            'snapshot_type': snapshot_type,
            'label': label or f'{snapshot_type}',
            'content_data': snapshot_data
        })

    def create_book_snapshot(self, book_id, snapshot_type='manual', label=''):
        """Create a snapshot of entire book's content."""
        nodes = self.db.get_all_node_contents(book_id)
        snapshot_data = {
            'nodes': [{'id': n['id'], 'title': n['title'], 'content': n['content']}
                      for n in nodes]
        }
        return self.db.create_snapshot({
            'book_id': book_id,
            'snapshot_type': snapshot_type,
            'label': label or '全书快照',
            'content_data': snapshot_data
        })

    def restore_node_snapshot(self, snapshot_id):
        """Restore a node's content from a snapshot."""
        snap = self.db.get_snapshot(snapshot_id)
        if not snap:
            return False
        data = json.loads(snap['content_data']) if isinstance(snap['content_data'], str) else snap['content_data']
        node_id = data.get('node_id') or snap.get('node_id')
        if not node_id:
            return False
        # Restore content
        self.db.save_node_content(node_id, {'content': data.get('content', '')})
        return True

    def preview_restore(self, snapshot_id):
        """Preview what will be restored from a snapshot."""
        snap = self.db.get_snapshot(snapshot_id)
        if not snap:
            return None
        data = json.loads(snap['content_data']) if isinstance(snap['content_data'], str) else snap['content_data']
        return {
            'snapshot_id': snapshot_id,
            'snapshot_type': snap.get('snapshot_type'),
            'created_at': snap.get('created_at'),
            'label': snap.get('label'),
            'content_preview': (data.get('content', '') or '')[:500],
            'has_versions': bool(data.get('versions')),
            'node_count': len(data.get('nodes', []))
        }

    def soft_delete_node(self, book_id, node_id):
        """Move a node to recycle bin instead of hard deleting."""
        node = self.db.get_node(node_id)
        if not node:
            return None
        content = self.db.get_node_content(node_id)
        item_data = {
            'node': node,
            'content': content.get('content', ''),
            'word_count': content.get('word_count', 0)
        }
        recycle_id = self.db.add_to_recycle_bin({
            'book_id': book_id,
            'item_type': 'node',
            'item_id': node_id,
            'item_data': item_data
        })
        self.db.delete_node(node_id)
        return recycle_id

    def soft_delete_item(self, book_id, item_type, item_id, item_data):
        """Generic soft delete for any item type."""
        return self.db.add_to_recycle_bin({
            'book_id': book_id,
            'item_type': item_type,
            'item_id': item_id,
            'item_data': item_data
        })

    def restore_from_recycle(self, recycle_id):
        """Restore an item from the recycle bin."""
        item = self.db.get_recycle_bin_item(recycle_id)
        if not item:
            return False
        data = json.loads(item['item_data']) if isinstance(item['item_data'], str) else item['item_data']
        item_type = item['item_type']

        if item_type == 'node':
            node_data = data.get('node', {})
            new_id = self.db.create_node(node_data)
            content = data.get('content', '')
            if content:
                self.db.save_node_content(new_id, {'content': content})
        elif item_type == 'lorebook':
            self.db.add_lorebook_entry(data)
        elif item_type == 'foreshadowing':
            self.db.add_foreshadowing(data)
        elif item_type == 'world_state':
            self.db.upsert_world_state(data)
        else:
            return False

        self.db.delete_recycle_bin_item(recycle_id)
        return True

    def cleanup(self, book_id, keep_count=50):
        """Clean up old snapshots beyond the retention limit."""
        self.db.cleanup_snapshots(book_id, keep_count=keep_count)
