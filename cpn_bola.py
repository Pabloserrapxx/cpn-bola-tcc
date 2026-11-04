from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple


@dataclass(frozen=True)
class Token:
    color: str
    value: Any

    def __str__(self) -> str:
        return f"{self.color}:{self.value}"


@dataclass
class Place:
    name: str
    marking: Counter = field(default_factory=Counter)

    def add(self, token: Token, amount: int = 1) -> None:
        if type(amount) is not int or amount <= 0:
            raise ValueError("A multiplicidade deve ser um inteiro positivo.")
        self.marking[token] += amount

    def has(self, token: Token, amount: int = 1) -> bool:
        return self.marking[token] >= amount

    def remove(self, token: Token, amount: int = 1) -> None:
        if type(amount) is not int or amount <= 0:
            raise ValueError("A multiplicidade deve ser um inteiro positivo.")
        if not self.has(token, amount):
            raise ValueError(f"{self.name}: token ausente: {token}")
        self.marking[token] -= amount
        if self.marking[token] <= 0:
            del self.marking[token]

    def tokens(self) -> List[Token]:
        return list(self.marking.elements())

    def clear(self) -> None:
        self.marking.clear()


@dataclass(frozen=True)
class ArcBinding:
    place: str
    token: Token


@dataclass
class Transition:
    name: str
    activity: str
    bind: Callable[["Event", "ColoredPetriNet"], Mapping[str, Any]]
    inputs: Callable[[Mapping[str, Any], "Event"], List[ArcBinding]]
    outputs: Callable[[Mapping[str, Any], "Event"], List[ArcBinding]]
    guard: Callable[[Mapping[str, Any], "Event"], bool] = lambda b, e: True
    postcondition: Callable[[Mapping[str, Any], "Event", "ColoredPetriNet"], bool] = (
        lambda b, e, cpn: True
    )


class DivergenceType(Enum):
    CONTROL_FLOW = "DIVERGENCIA_FLUXO_CONTROLE"
    DATA_FLOW = "DIVERGENCIA_FLUXO_DADOS"
    RULE_VIOLATION = "VIOLACAO_REGRA"
    CORRUPTED_RESOURCE = "RECURSO_CORROMPIDO"
    UNKNOWN_ACTIVITY = "ATIVIDADE_DESCONHECIDA"
    INVALID_EVENT = "EVENTO_INVALIDO"
    NO_MODELED_EVENTS = "SEM_EVENTOS_MODELADOS"


class InvalidEvent(ValueError):
    """Informação obrigatória ausente ou inválida no log."""


class InvalidResource(ValueError):
    """Resposta não satisfaz a informação de recurso esperada pelo modelo."""


@dataclass(frozen=True)
class Divergence:
    kind: DivergenceType
    trace_id: str
    event_index: int
    activity: str
    message: str


@dataclass
class ReplayResult:
    trace_id: str
    accepted: bool
    divergences: List[Divergence]
    final_marking: Dict[str, List[Token]]
    evaluated_events: int = 0
    skipped_events: int = 0
    unprocessed_events: int = 0
    steps: List[Dict[str, Any]] = field(default_factory=list)
    backend: str = "snakes"


@dataclass(frozen=True)
class Event:
    method: str
    path: str
    status_code: int
    user_id: str
    path_params: Mapping[str, Any] = field(default_factory=dict)
    request_body: Any = field(default_factory=dict)
    response_body: Any = field(default_factory=dict)
    query_params: Mapping[str, Any] = field(default_factory=dict)
    request_headers: Mapping[str, Any] = field(default_factory=dict)
    response_headers: Mapping[str, Any] = field(default_factory=dict)
    source_index: Optional[int] = None

    @property
    def activity(self) -> str:
        return activity_name(self.method, self.path, self.status_code)


@dataclass(frozen=True)
class Trace:
    trace_id: str
    user_id: str
    events: Tuple[Event, ...]


def activity_name(method: str, path: str, status_code: int) -> str:
    return f"{method.lower()}-{path}-{status_code}"


class ColoredPetriNet:
    def __init__(self, name: str):
        self.name = name
        self.places: Dict[str, Place] = {}
        self.transitions: Dict[str, Transition] = {}
        self.activity_index: Dict[str, str] = {}
        self.initial_places: Tuple[str, ...] = ("p_user",)
        self.metadata: Dict[str, Any] = {}

    def add_place(self, name: str) -> None:
        if name in self.places:
            raise ValueError(f"Lugar duplicado: {name}")
        self.places[name] = Place(name)

    def add_transition(self, transition: Transition) -> None:
        if transition.name in self.transitions:
            raise ValueError(f"Transição duplicada: {transition.name}")
        if transition.activity in self.activity_index:
            raise ValueError(f"Atividade ambígua: {transition.activity}")
        self.transitions[transition.name] = transition
        self.activity_index[transition.activity] = transition.name

    def select_transition(self, activity: str) -> Optional[Transition]:
        name = self.activity_index.get(activity)
        return None if name is None else self.transitions[name]

    def reset(self) -> None:
        for place in self.places.values():
            place.clear()

    def snapshot(self) -> Dict[str, List[Token]]:
        return {name: place.tokens() for name, place in self.places.items()}

    def clone(self) -> "ColoredPetriNet":
        return deepcopy(self)


def user_token(user_id: str) -> Token:
    return Token("USER", user_id)


def account_token(user_id: str, account_id: int) -> Token:
    return Token("ACCOUNT_ID", (user_id, account_id))


def account_state_token(user_id: str, account_id: int) -> Token:
    return Token("ACCOUNT_STATE", (user_id, account_id))


def popula_lugares_iniciais(cpn: ColoredPetriNet, initial_places: Iterable[str], trace: Trace) -> None:
    for place_name in initial_places:
        if place_name == "p_user":
            cpn.places[place_name].add(user_token(trace.user_id))
        else:
            raise ValueError(f"Inicialização não definida para o lugar {place_name}")


def seleciona_transicao(cpn: ColoredPetriNet, activity: str) -> Optional[Transition]:
    return cpn.select_transition(activity)


def seleciona_binding(transition: Transition, cpn: ColoredPetriNet, event: Event) -> Mapping[str, Any]:
    return transition.bind(event, cpn)


def _missing_inputs(cpn: ColoredPetriNet, required: List[ArcBinding]) -> List[ArcBinding]:
    return [arc for arc, count in Counter(required).items()
            if not cpn.places[arc.place].has(arc.token, count)]


def _belongs_to(token: Token, user_id: str) -> bool:
    return (token.value == user_id if token.color == "USER" else
            isinstance(token.value, tuple) and bool(token.value) and token.value[0] == user_id)


def divergencia_fluxo_controle(cpn: ColoredPetriNet, transition: Transition,
                               binding: Mapping[str, Any], event: Event) -> Tuple[bool, str]:
    missing = _missing_inputs(cpn, transition.inputs(binding, event))
    if not missing:
        return False, ""
    required = Counter(arc.place for arc in transition.inputs(binding, event))
    for place_name, count in required.items():
        available = sum(n for tok, n in cpn.places[place_name].marking.items()
                        if _belongs_to(tok, event.user_id))
        if available < count:
            return True, (f"{place_name}: faltam recursos prévios do usuário "
                          f"{event.user_id} (necessários={count}, disponíveis={available}).")
    return False, ""


def divergencia_fluxo_dados(cpn: ColoredPetriNet, transition: Transition,
                             binding: Mapping[str, Any], event: Event) -> Tuple[bool, str]:
    missing = _missing_inputs(cpn, transition.inputs(binding, event))
    if not missing:
        return False, ""
    if missing:
        arc = missing[0]
        candidates = [str(tok) for tok in cpn.places[arc.place].tokens()
                      if _belongs_to(tok, event.user_id)]
        return True, (f"{arc.place}: observado={arc.token}; "
                      f"recursos permitidos para {event.user_id}={candidates}.")
    return False, ""


def violacao_regra(transition: Transition, binding: Mapping[str, Any], event: Event) -> Tuple[bool, str]:
    if transition.guard(binding, event):
        return False, ""
    return True, "A guarda/regra da transição foi violada."


def fire(cpn: ColoredPetriNet, transition: Transition,
         binding: Mapping[str, Any], event: Event) -> None:
    inputs = transition.inputs(binding, event)
    if not transition.guard(binding, event):
        raise ValueError("A guarda da transição foi violada.")
    missing = _missing_inputs(cpn, inputs)
    if missing:
        raise RuntimeError(f"Transição não habilitada; inputs ausentes: {missing}")
    outputs = transition.outputs(binding, event)
    # Avaliar e validar TODOS os arcos antes de modificar a marcação.
    for arc in inputs + outputs:
        if arc.place not in cpn.places:
            raise ValueError(f"Lugar inexistente: {arc.place}")
        hash(arc.token)
    for arc in inputs:
        cpn.places[arc.place].remove(arc.token)
    for arc in outputs:
        cpn.places[arc.place].add(arc.token)


def recurso_corrompido(cpn: ColoredPetriNet, transition: Transition,
                        binding: Mapping[str, Any], event: Event) -> Tuple[bool, str]:
    if transition.postcondition(binding, event, cpn):
        return False, ""
    return True, "O recurso observado diverge do estado esperado após o fire."


def replay_trace(template_cpn: ColoredPetriNet, initial_places: Iterable[str], trace: Trace,
                 *, unknown_policy: str = "error", record_steps: bool = False,
                 backend: str = "snakes", continue_after_divergence: bool = False) -> ReplayResult:
    if unknown_policy not in ("error", "skip"):
        raise ValueError("unknown_policy deve ser error ou skip")
    if backend not in ("snakes", "python"):
        raise ValueError("backend deve ser snakes ou python")
    cpn = template_cpn.clone()
    cpn.reset()
    popula_lugares_iniciais(cpn, initial_places, trace)
    executor = None
    if backend == "snakes":
        from cpn_snakes import SnakesExecutor
        executor = SnakesExecutor(cpn)
    divergences: List[Divergence] = []
    evaluated = skipped = visited = 0
    steps = []

    for event_index, event in enumerate(trace.events):
        visited += 1
        if cpn.metadata.get("routes"):
            from cpn_openapi import normalize_event, select_event_status
            try:
                event = select_event_status(cpn, normalize_event(cpn, event))
            except InvalidEvent as exc:
                divergences.append(Divergence(DivergenceType.INVALID_EVENT, trace.trace_id,
                                   event_index, event.activity, str(exc)))
                if continue_after_divergence:
                    continue
                break
        if not event.user_id or event.user_id != trace.user_id:
            divergences.append(Divergence(DivergenceType.INVALID_EVENT, trace.trace_id,
                               event_index, event.activity, "Usuário do evento difere do trace."))
            if continue_after_divergence:
                continue
            break
        transition = seleciona_transicao(cpn, event.activity)
        if transition is None:
            if unknown_policy == "skip":
                skipped += 1
                continue
            divergences.append(Divergence(
                DivergenceType.UNKNOWN_ACTIVITY, trace.trace_id, event_index,
                event.activity, "Nenhuma transição corresponde à atividade observada."
            ))
            if continue_after_divergence:
                continue
            break

        evaluated += 1
        try:
            binding = seleciona_binding(transition, cpn, event)
        except InvalidEvent as exc:
            divergences.append(Divergence(DivergenceType.INVALID_EVENT, trace.trace_id,
                               event_index, event.activity, str(exc)))
            if continue_after_divergence:
                continue
            break

        is_div, msg = divergencia_fluxo_controle(cpn, transition, binding, event)
        if is_div:
            divergences.append(Divergence(DivergenceType.CONTROL_FLOW, trace.trace_id,
                                          event_index, event.activity, msg))
            if continue_after_divergence:
                continue
            break

        is_div, msg = divergencia_fluxo_dados(cpn, transition, binding, event)
        if is_div:
            divergences.append(Divergence(DivergenceType.DATA_FLOW, trace.trace_id,
                                          event_index, event.activity, msg))
            if continue_after_divergence:
                continue
            break

        is_div, msg = violacao_regra(transition, binding, event)
        if is_div:
            divergences.append(Divergence(DivergenceType.RULE_VIOLATION, trace.trace_id,
                                          event_index, event.activity, msg))
            if continue_after_divergence:
                continue
            break

        try:
            if executor is None:
                fire(cpn, transition, binding, event)
            else:
                executor.fire(transition, binding, event)
        except InvalidResource as exc:
            divergences.append(Divergence(DivergenceType.CORRUPTED_RESOURCE, trace.trace_id,
                               event_index, event.activity, str(exc)))
            if continue_after_divergence:
                continue
            break

        is_div, msg = recurso_corrompido(cpn, transition, binding, event)
        if record_steps:
            steps.append({"event_index": event_index, "activity": event.activity,
                          "marking": cpn.snapshot()})
        if is_div:
            divergences.append(Divergence(DivergenceType.CORRUPTED_RESOURCE, trace.trace_id,
                                          event_index, event.activity, msg))
            if continue_after_divergence:
                continue
            break

    if not evaluated and not divergences:
        divergences.append(Divergence(DivergenceType.NO_MODELED_EVENTS, trace.trace_id,
                                     -1, "", "Trace sem eventos avaliáveis pelo modelo."))
    return ReplayResult(trace.trace_id, not divergences, divergences, cpn.snapshot(),
                        evaluated, skipped, len(trace.events) - visited, steps, backend)


@dataclass
class LogReplaySummary:
    results: List[ReplayResult]
    error_trace_ids: List[str]
    fitness: Optional[float]


def verificar_conformidade(cpn: ColoredPetriNet, initial_places: Iterable[str],
                            traces: Iterable[Trace], **replay_options) -> LogReplaySummary:
    traces = list(traces)
    initial_places = tuple(initial_places)
    results = [replay_trace(cpn, initial_places, trace, **replay_options) for trace in traces]
    error_trace_ids = [r.trace_id for r in results if not r.accepted]
    fitness = None if not traces else 1.0 - (len(error_trace_ids) / len(traces))
    return LogReplaySummary(results, error_trace_ids, fitness)


def build_bola_cpn() -> ColoredPetriNet:
    cpn = ColoredPetriNet("BOLA-CPN")
    cpn.add_place("p_user")
    cpn.add_place("p_account_id")
    cpn.add_place("p_account_state")

    # POST /login -> 200
    def bind_login(event: Event, cpn: ColoredPetriNet) -> Mapping[str, Any]:
        if not isinstance(event.response_body, dict):
            raise InvalidEvent("Resposta de login deve ser um objeto.")
        auth = event.response_body.get("authentication", {})
        if not isinstance(auth, dict):
            raise InvalidEvent("authentication deve ser um objeto.")
        account_id = auth.get("id", event.response_body.get("id"))
        if type(account_id) is not int:
            raise InvalidEvent("Identificador da conta ausente ou não inteiro na resposta de login.")
        return {"user_id": event.user_id, "account_id": account_id}

    def login_inputs(binding, event):
        return [ArcBinding("p_user", user_token(binding["user_id"]))]

    def login_outputs(binding, event):
        return [
            ArcBinding("p_user", user_token(binding["user_id"])),
            ArcBinding("p_account_id", account_token(binding["user_id"], binding["account_id"])),
        ]

    def login_guard(binding, event):
        return bool(binding["user_id"]) and type(binding["account_id"]) is int and binding["account_id"] > 0

    def login_postcondition(binding, event, cpn):
        return cpn.places["p_account_id"].has(account_token(binding["user_id"], binding["account_id"]))

    cpn.add_transition(Transition(
        "t_login_200", activity_name("POST", "/login", 200), bind_login,
        login_inputs, login_outputs, login_guard, login_postcondition
    ))

    # GET /accounts/{id} -> 200
    def bind_get(event: Event, cpn: ColoredPetriNet) -> Mapping[str, Any]:
        if type(event.path_params.get("id")) is not int:
            raise InvalidEvent("Parâmetro id deve ser inteiro.")
        return {"user_id": event.user_id, "account_id": event.path_params.get("id")}

    def get_inputs(binding, event):
        return [
            ArcBinding("p_user", user_token(binding["user_id"])),
            ArcBinding("p_account_id", account_token(binding["user_id"], binding["account_id"])),
        ]

    def get_outputs(binding, event):
        return [
            ArcBinding("p_user", user_token(binding["user_id"])),
            ArcBinding("p_account_id", account_token(binding["user_id"], binding["account_id"])),
            ArcBinding("p_account_state", account_state_token(binding["user_id"], binding["account_id"])),
        ]

    def get_guard(binding, event):
        return bool(binding["user_id"]) and type(binding["account_id"]) is int and binding["account_id"] > 0

    def get_postcondition(binding, event, cpn):
        if not isinstance(event.response_body, dict):
            return False
        observed_id = event.response_body.get("id")
        if type(observed_id) is not int or observed_id != binding["account_id"]:
            return False
        return cpn.places["p_account_state"].has(
            account_state_token(binding["user_id"], binding["account_id"])
        )

    cpn.add_transition(Transition(
        "t_get_account_200", activity_name("GET", "/accounts/{id}", 200), bind_get,
        get_inputs, get_outputs, get_guard, get_postcondition
    ))
    return cpn


def print_marking(marking: Dict[str, List[Token]]) -> None:
    for place, tokens in marking.items():
        print(f"  {place}:")
        if not tokens:
            print("    ∅")
        else:
            for token in tokens:
                print(f"    ● {token}")


def print_result(result: ReplayResult) -> None:
    print(f"\nTrace: {result.trace_id}")
    print(f"Resultado: {'ACEITO' if result.accepted else 'DIVERGENTE'}")
    for div in result.divergences:
        print(f"  Tipo: {div.kind.value}")
        print(f"  Evento #{div.event_index}: {div.activity}")
        print(f"  Motivo: {div.message}")
    print("Marcação final:")
    print_marking(result.final_marking)


if __name__ == "__main__":
    from cpn_cli import main
    raise SystemExit(main())
