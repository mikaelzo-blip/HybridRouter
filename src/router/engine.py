import ast
import fnmatch
from typing import Any
from pydantic import BaseModel, Field
from src.schemas import FullRouterConfigFile, RequestMetadata, RouteDecision, RouterRule
from src.router.transforms import apply_transforms


class RoutingContext(BaseModel):
    metadata: RequestMetadata = Field(default_factory=RequestMetadata)
    total_tokens: int = 0
    messages: list[dict[str, Any]] = Field(default_factory=list)
    request: dict[str, Any] = Field(default_factory=dict)


class _FilesProxy:
    def __init__(self, targets: list[str]):
        self.target = targets


class _RequestProxy:
    def __init__(self, total_tokens: int):
        self.total_tokens = total_tokens


class _ContextProxy:
    def __init__(self, metadata: RequestMetadata):
        self.metadata = metadata


def _match_glob(path: str, pattern: str) -> bool:
    norm_path = path.replace("\\", "/")
    norm_pat = pattern.replace("\\", "/")
    if norm_pat.endswith("/**"):
        prefix = norm_pat[:-3]
        return norm_path == prefix or norm_path.startswith(prefix + "/")
    return fnmatch.fnmatch(norm_path, norm_pat)


def _matches_any(targets: list[str], patterns: list[str]) -> bool:
    for t in targets:
        for p in patterns:
            if _match_glob(t, p):
                return True
    return False


def _has_intent(requested_intents: list[str], actual_intents: list[str]) -> bool:
    actual_set = set(actual_intents)
    return any(req in actual_set for req in requested_intents)


def _safe_eval_node(node: ast.AST, env: dict[str, Any]) -> Any:
    # AST-based safe evaluation of router conditions without unrestricted eval
    if isinstance(node, ast.Expression):
        return _safe_eval_node(node.body, env)
    elif isinstance(node, ast.Constant):
        return node.value
    elif isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        raise ValueError(f"Unknown variable in condition: {node.id}")
    elif isinstance(node, ast.Attribute):
        if node.attr.startswith("__"):
            raise ValueError(f"Dunder attributes are not allowed: {node.attr}")
        value = _safe_eval_node(node.value, env)
        if hasattr(value, node.attr):
            return getattr(value, node.attr)
        elif isinstance(value, dict) and node.attr in value:
            return value[node.attr]
        raise AttributeError(f"Attribute {node.attr} not found on {type(value)}")
    elif isinstance(node, ast.UnaryOp):
        operand = _safe_eval_node(node.operand, env)
        if isinstance(node.op, ast.Not):
            return not operand
        elif isinstance(node.op, ast.USub):
            return -operand
        raise ValueError(f"Unsupported unary operator: {type(node.op)}")
    elif isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            for val_node in node.values:
                if not _safe_eval_node(val_node, env):
                    return False
            return True
        elif isinstance(node.op, ast.Or):
            for val_node in node.values:
                if _safe_eval_node(val_node, env):
                    return True
            return False
        raise ValueError(f"Unsupported boolean operator: {type(node.op)}")
    elif isinstance(node, ast.Compare):
        left = _safe_eval_node(node.left, env)
        for op, comparator in zip(node.ops, node.comparators):
            right = _safe_eval_node(comparator, env)
            if isinstance(op, ast.Eq) and not (left == right):
                return False
            elif isinstance(op, ast.NotEq) and not (left != right):
                return False
            elif isinstance(op, ast.Lt) and not (left < right):
                return False
            elif isinstance(op, ast.LtE) and not (left <= right):
                return False
            elif isinstance(op, ast.Gt) and not (left > right):
                return False
            elif isinstance(op, ast.GtE) and not (left >= right):
                return False
            elif isinstance(op, ast.In) and not (left in right):
                return False
            elif isinstance(op, ast.NotIn) and not (left not in right):
                return False
            left = right
        return True
    elif isinstance(node, ast.List):
        return [_safe_eval_node(elt, env) for elt in node.elts]
    elif isinstance(node, ast.Call):
        func = _safe_eval_node(node.func, env)
        if not callable(func):
            raise ValueError(f"Target {func} is not callable")
        args = [_safe_eval_node(arg, env) for arg in node.args]
        return func(*args)
    raise ValueError(f"Unsupported AST node in condition: {type(node)}")


class RouterEngine:
    def __init__(self, config: FullRouterConfigFile):
        self.config = config
        # Ensure rules are strictly sorted by priority descending
        self.rules: list[RouterRule] = sorted(
            config.router.rules,
            key=lambda r: r.priority,
            reverse=True
        )

    def _eval_condition(self, condition: str, ctx: RoutingContext) -> bool:
        cond = condition.strip()
        if cond.lower() == "default":
            return True

        files_proxy = _FilesProxy(ctx.metadata.files_target)
        request_proxy = _RequestProxy(ctx.total_tokens)
        context_proxy = _ContextProxy(ctx.metadata)

        env: dict[str, Any] = {
            "True": True,
            "False": False,
            "context": context_proxy,
            "request": request_proxy,
            "files": files_proxy,
            "matches_any": lambda t, p: _matches_any(t, p),
            "has_intent": lambda reqs: _has_intent(reqs, ctx.metadata.intent),
            "default": True,
        }

        try:
            tree = ast.parse(cond, mode="eval")
            return bool(_safe_eval_node(tree, env))
        except Exception:
            return False

    def route(self, ctx: RoutingContext) -> RouteDecision:
        for rule in self.rules:
            if self._eval_condition(rule.condition, ctx):
                transformed_msgs, applied = apply_transforms(rule.context_transforms, ctx.messages)
                target = rule.target or (rule.pipeline[0].target if rule.pipeline else "gemini_executor")
                return RouteDecision(
                    rule_name=rule.name,
                    target_model=target,
                    pipeline=rule.pipeline,
                    applied_transforms=applied,
                    transformed_messages=transformed_msgs if applied else None,
                    escalation_reason=f"Matched rule '{rule.name}' with priority {rule.priority}"
                )

        # Fallback default
        return RouteDecision(
            rule_name="default_fallback",
            target_model="gemini_executor",
            applied_transforms=[],
            transformed_messages=None,
            escalation_reason="Default fallback"
        )
