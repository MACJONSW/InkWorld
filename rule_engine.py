"""
写作规则中心引擎
"""


class RuleEngine:
    def __init__(self, db):
        self.db = db

    def get_active_rules(self, book_id, node_id=None):
        """Get all active rules for a given scope, handling inheritance.
        Rules cascade: book > volume > chapter.
        More specific scope rules override general ones with same category+title.
        """
        all_rules = self.db.get_writing_rules(book_id)
        active_rules = [r for r in all_rules if r.get('enabled')]

        if not node_id:
            # Book scope only
            return [r for r in active_rules if r.get('scope_type') == 'book' or not r.get('scope_node_id')]

        # Build node ancestry
        ancestors = self._get_node_ancestors(node_id)
        ancestor_ids = {a['id'] for a in ancestors}
        ancestor_ids.add(node_id)

        # Filter rules: include book-scope + rules scoped to current node or ancestors
        applicable = []
        for r in active_rules:
            scope = r.get('scope_type', 'book')
            scope_node = r.get('scope_node_id')
            if scope == 'book' or not scope_node:
                applicable.append(r)
            elif scope_node in ancestor_ids:
                applicable.append(r)

        # Deduplicate: more specific scope wins
        seen = {}
        for r in sorted(applicable, key=lambda x: self._scope_priority(x, node_id, ancestors)):
            key = (r.get('category'), r.get('title'))
            seen[key] = r

        return sorted(seen.values(), key=lambda x: -x.get('priority', 0))

    def _get_node_ancestors(self, node_id):
        """Walk up the tree to get all ancestor nodes."""
        ancestors = []
        current = self.db.get_node(node_id)
        while current and current.get('parent_id'):
            parent = self.db.get_node(current['parent_id'])
            if parent:
                ancestors.append(parent)
                current = parent
            else:
                break
        return ancestors

    def _scope_priority(self, rule, node_id, ancestors):
        """Higher number = more specific = higher priority."""
        scope_node = rule.get('scope_node_id')
        if not scope_node or rule.get('scope_type') == 'book':
            return 0
        if scope_node == node_id:
            return 2
        return 1

    def build_rule_prompt(self, book_id, node_id=None):
        """Build a prompt text from active rules for injection into LLM context."""
        rules = self.get_active_rules(book_id, node_id)
        if not rules:
            return ''

        category_labels = {
            'style': '文风规则',
            'narrative': '叙事规则',
            'character_speech': '角色语言规则',
            'format': '格式规则',
            'prohibition': '禁用规则',
            'hard_lock': '硬设定锁定',
            'exception': '例外规则'
        }

        sections = {}
        for r in rules:
            cat = r.get('category', 'style')
            label = category_labels.get(cat, cat)
            if label not in sections:
                sections[label] = []
            sections[label].append(f"- {r.get('title', '')}: {r.get('content', '')}")

        parts = ['=== 写作规则 ===']
        for label, items in sections.items():
            parts.append(f'\n【{label}】')
            parts.extend(items)

        return '\n'.join(parts)

    def validate_against_rules(self, book_id, text, node_id=None):
        """Check if text violates any active rules. Returns violation list.
        This returns rule descriptions for LLM-based validation.
        """
        rules = self.get_active_rules(book_id, node_id)
        prohibitions = [r for r in rules if r.get('category') == 'prohibition']
        hard_locks = [r for r in rules if r.get('category') == 'hard_lock']

        violations = []
        for rule in prohibitions:
            content = rule.get('content', '')
            # Simple keyword check for prohibition rules
            keywords = [k.strip() for k in content.split('，') if k.strip()]
            keywords += [k.strip() for k in content.split(',') if k.strip()]
            for kw in keywords:
                if len(kw) >= 2 and kw in text:
                    violations.append({
                        'rule_id': rule['id'],
                        'rule_title': rule.get('title', ''),
                        'category': 'prohibition',
                        'severity': 'medium',
                        'description': f'文本中出现禁用内容: "{kw}"',
                        'matched_text': kw
                    })

        return violations

    def check_rule_conflicts(self, book_id):
        """Check for conflicts between rules (e.g., contradicting style rules)."""
        rules = self.db.get_writing_rules(book_id)
        conflicts = []
        # Group by category
        by_cat = {}
        for r in rules:
            cat = r.get('category', '')
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(r)

        # Check for same-category rules with overlapping scope
        for cat, cat_rules in by_cat.items():
            for i, r1 in enumerate(cat_rules):
                for r2 in cat_rules[i+1:]:
                    if (r1.get('scope_type') == r2.get('scope_type') and
                        r1.get('scope_node_id') == r2.get('scope_node_id') and
                        r1.get('enabled') and r2.get('enabled')):
                        conflicts.append({
                            'rule1': {'id': r1['id'], 'title': r1.get('title', '')},
                            'rule2': {'id': r2['id'], 'title': r2.get('title', '')},
                            'reason': f'同一范围内存在多条同类规则({cat})'
                        })

        return conflicts
