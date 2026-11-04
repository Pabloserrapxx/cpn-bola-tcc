"""Métricas por requisição rotulada; desconhecidos permanecem sem classificação."""
from collections import Counter

# Política de alerta declarada: controle/dados são suspeitas de acesso ao objeto.
# Falhas de schema, pós-condição, captura e exclusões não viram BOLA automaticamente.
BOLA_DIVERGENCES = {'DIVERGENCIA_FLUXO_CONTROLE', 'DIVERGENCIA_FLUXO_DADOS'}


def prediction(row):
    status = row.get('status')
    if status == 'fired':
        return False
    if status == 'divergent':
        kind = (row.get('result', {}).get('divergence') or {}).get('kind')
        if kind in BOLA_DIVERGENCES:
            return True
    return None


def metrics(rows):
    counts = Counter(TP=0, FP=0, FN=0, TN=0)
    unknown = attacks = legitimate = 0
    for row in rows:
        truth, predicted = row['expected_bola'], row['predicted_bola']
        if type(truth) is not bool or (predicted is not None and type(predicted) is not bool):
            raise ValueError('Gabarito deve ser booleano; predição deve ser booleana ou None.')
        attacks += truth
        legitimate += not truth
        if predicted is None:
            unknown += 1
        else:
            counts['TP' if truth and predicted else 'FN' if truth else 'FP' if predicted else 'TN'] += 1
    def ratio(numerator, denominator):
        return numerator / denominator if denominator else None
    tp, fp, fn, tn = (counts[k] for k in ('TP', 'FP', 'FN', 'TN'))
    classified = tp + fp + fn + tn
    return dict(total=len(rows), attacks=attacks, legitimate=legitimate,
        classified=classified, unclassified=unknown, coverage=ratio(classified, len(rows)),
        **counts, precision=ratio(tp, tp+fp), recall=ratio(tp, tp+fn),
        f1=ratio(2*tp, 2*tp+fp+fn), accuracy=ratio(tp+tn, classified),
        end_to_end_attack_detection=ratio(tp, attacks))


def aggregate(rows):
    scenarios = sorted({r['scenario'] for r in rows})
    return dict(overall=metrics(rows), by_scenario={
        scenario: metrics([r for r in rows if r['scenario'] == scenario]) for scenario in scenarios})
