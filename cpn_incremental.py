"""Replay persistente por sessão; sem releitura do histórico ou reset por evento.

Uma sessão deve receber chamadas sequenciais. Não há persistência em disco,
expiração automática ou coordenação entre processos nesta etapa.
"""
from dataclasses import dataclass
from time import perf_counter_ns

from cpn_bola import (
    Divergence, DivergenceType, InvalidEvent, InvalidResource, ReplayResult, Trace,
    divergencia_fluxo_controle, divergencia_fluxo_dados, fire,
    popula_lugares_iniciais, recurso_corrompido, seleciona_binding,
    seleciona_transicao, violacao_regra,
)
from cpn_openapi import normalize_event, select_event_status


@dataclass(frozen=True)
class EventResult:
    event_index: int
    source_index: int | None
    activity: str
    status: str  # fired, divergent, outside_model, not_processed
    fired: bool
    divergence: Divergence | None
    elapsed_ns: int


class ReplaySession:
    def __init__(self, model, trace_id, user_id, *, initial_places=None,
                 unknown_policy='error', backend='snakes',
                 continue_after_divergence=False, record_steps=False):
        if unknown_policy not in ('error', 'skip'):
            raise ValueError('unknown_policy deve ser error ou skip')
        if backend not in ('snakes', 'python'):
            raise ValueError('backend deve ser snakes ou python')
        if not user_id:
            raise ValueError('Identidade da sessão ausente.')
        self.trace_id, self.user_id = trace_id, user_id
        self.unknown_policy, self.backend = unknown_policy, backend
        self.continue_after_divergence = continue_after_divergence
        self.record_steps = record_steps
        self.cpn = model.clone()
        self.cpn.reset()
        popula_lugares_iniciais(self.cpn,
            model.initial_places if initial_places is None else tuple(initial_places),
            Trace(trace_id, user_id, ()))
        self.executor = None
        if backend == 'snakes':
            from cpn_snakes import SnakesExecutor
            self.executor = SnakesExecutor(self.cpn)
        self.divergences, self.steps = [], []
        self.received = self.evaluated = self.skipped = self.unprocessed = 0
        self.halted = self.closed = False

    def process(self, event):
        if self.closed:
            raise ValueError('Sessão encerrada; crie uma nova sessão.')
        started = perf_counter_ns()
        index = self.received
        self.received += 1
        fired = False

        def result(status, divergence=None):
            if divergence is not None:
                self.divergences.append(divergence)
                if not self.continue_after_divergence:
                    self.halted = True
            return EventResult(index, event.source_index, event.activity, status,
                               fired, divergence, perf_counter_ns() - started)

        def fail(kind, message):
            return result('divergent', Divergence(kind, self.trace_id, index,
                                                 event.activity, message))

        if self.halted:
            self.unprocessed += 1
            return result('not_processed')
        if self.cpn.metadata.get('routes'):
            try:
                event = select_event_status(self.cpn, normalize_event(self.cpn, event))
            except InvalidEvent as exc:
                return fail(DivergenceType.INVALID_EVENT, str(exc))
        if not event.user_id or event.user_id != self.user_id:
            return fail(DivergenceType.INVALID_EVENT, 'Usuário do evento difere do trace.')
        transition = seleciona_transicao(self.cpn, event.activity)
        if transition is None:
            if self.unknown_policy == 'skip':
                self.skipped += 1
                return result('outside_model')
            return fail(DivergenceType.UNKNOWN_ACTIVITY,
                        'Nenhuma transição corresponde à atividade observada.')
        self.evaluated += 1
        try:
            binding = seleciona_binding(transition, self.cpn, event)
        except InvalidEvent as exc:
            return fail(DivergenceType.INVALID_EVENT, str(exc))
        for check, kind in ((divergencia_fluxo_controle, DivergenceType.CONTROL_FLOW),
                            (divergencia_fluxo_dados, DivergenceType.DATA_FLOW)):
            invalid, message = check(self.cpn, transition, binding, event)
            if invalid:
                return fail(kind, message)
        invalid, message = violacao_regra(transition, binding, event)
        if invalid:
            return fail(DivergenceType.RULE_VIOLATION, message)
        try:
            if self.executor is None:
                fire(self.cpn, transition, binding, event)
            else:
                self.executor.fire(transition, binding, event)
        except InvalidResource as exc:
            return fail(DivergenceType.CORRUPTED_RESOURCE, str(exc))
        fired = True
        invalid, message = recurso_corrompido(self.cpn, transition, binding, event)
        if self.record_steps:
            self.steps.append(dict(event_index=index, activity=event.activity,
                                   marking=self.cpn.snapshot()))
        if invalid:
            return fail(DivergenceType.CORRUPTED_RESOURCE, message)
        return result('fired')

    def snapshot(self):
        """Cópia da marcação; só obtenha por evento para diagnóstico/comparação."""
        return self.cpn.snapshot()

    def finish(self):
        """Encerra o trace, inclusive vazio/totalmente fora do modelo; idempotente."""
        if not self.closed:
            if not self.evaluated and not self.divergences:
                self.divergences.append(Divergence(DivergenceType.NO_MODELED_EVENTS,
                    self.trace_id, -1, '', 'Trace sem eventos avaliáveis pelo modelo.'))
            self.closed = True
        return ReplayResult(self.trace_id, not self.divergences, list(self.divergences),
            self.snapshot(), self.evaluated, self.skipped, self.unprocessed,
            list(self.steps), self.backend)
