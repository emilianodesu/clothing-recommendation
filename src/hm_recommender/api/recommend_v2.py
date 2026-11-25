"""Real-time two-stage v2 recommendation engine (<40 ms CPU, <4 GiB RAM, zero sklearn)."""
from __future__ import annotations

import os

os.environ.setdefault('OPENBLAS_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
os.environ.setdefault('OMP_NUM_THREADS', '1')

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
import gc
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any
from uuid import uuid4

import numpy as np
import polars as pl
from pydantic import BaseModel
from scipy import sparse

from hm_recommender.api.schemas import RecommendationItem, RecommendationRequest, RecommendationResponse
from hm_recommender.v2.features import V2FeatureContext
from hm_recommender.v2.latent import LatentContentIndex
from hm_recommender.v2.package import verify_v2_release
from hm_recommender.v2.ranker import load_booster, predict_scores
from hm_recommender.v2.retrieval import (
    AGE_BINS,
    CANDIDATE_SCHEMA,
    EVENT_KIND_WEIGHTS,
    MISSING_RANK_SENTINEL,
    RANK_COLUMNS,
    REASON_PRIORITY,
    SCORE_COLUMNS,
    RetrievalConfig,
    _AGE_BIN_TO_CODE,
    _ALL_SEG,
    _UNKNOWN_AGE_CODE,
    _row_l1_normalize,
)

FILTERS: tuple[str, ...] = ('product_type_name', 'product_group_name', 'colour_group_name')


def _build_variant_matrix_from_catalog(
    product_codes: Sequence[str],
    item_count14: np.ndarray,
    variant_per_code_cap: int,
) -> sparse.csr_matrix:
    n_articles = len(product_codes)
    if n_articles == 0:
        return sparse.csr_matrix((0, 0), dtype=np.float32)

    groups: dict[str, list[int]] = {}
    for node_i, pc in enumerate(product_codes):
        if pc:
            groups.setdefault(pc, []).append(node_i)

    pop14d = item_count14.astype(np.float32, copy=False)
    rows_list: list[np.ndarray] = []
    cols_list: list[np.ndarray] = []
    vals_list: list[np.ndarray] = []
    for node_group in groups.values():
        if len(node_group) < 2:
            continue
        nodes_arr = np.asarray(node_group, dtype=np.int32)
        order = np.lexsort((nodes_arr, -pop14d[nodes_arr]))
        ordered_nodes = nodes_arr[order]
        targets = ordered_nodes[:variant_per_code_cap]
        target_weights = (1.0 + np.log1p(pop14d[targets])).astype(np.float32)
        for src in ordered_nodes:
            keep = targets != src
            if np.any(keep):
                t_kept = targets[keep]
                w_kept = target_weights[keep]
                rows_list.append(np.full(t_kept.size, src, dtype=np.int32))
                cols_list.append(t_kept)
                vals_list.append(w_kept)

    if not rows_list:
        return sparse.csr_matrix((n_articles, n_articles), dtype=np.float32)

    mat = sparse.csr_matrix(
        (np.concatenate(vals_list), (np.concatenate(rows_list), np.concatenate(cols_list))),
        shape=(n_articles, n_articles),
        dtype=np.float32,
    )
    mat.sum_duplicates()
    mat.sort_indices()
    return mat


def _event_utc_date(raw_time: object) -> date | None:
    if raw_time is None:
        return None
    if isinstance(raw_time, datetime):
        dt = raw_time.astimezone(timezone.utc) if raw_time.tzinfo is not None else raw_time
        return dt.date()
    if isinstance(raw_time, date):
        return raw_time
    if isinstance(raw_time, str):
        parsed = datetime.fromisoformat(raw_time)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.date()
    return None


@dataclass(frozen=True)
class TwoStageState:
    release_id: str
    data_as_of: date
    customers: frozenset[str]
    catalog: frozenset[str]
    popularity: tuple[str, ...]
    filter_index: MappingProxyType
    _article_ids: tuple[str, ...]
    _article_to_idx: dict[str, int]
    _product_codes: tuple[str, ...]
    _item_count7: np.ndarray
    _global_pop_order: np.ndarray
    _pop7d_nodes: np.ndarray
    _pop7d_scores: np.ndarray
    _pop7d_ranks: np.ndarray
    _user_age_bin_codes: np.ndarray
    _user_pref_ig_codes: np.ndarray
    _ig_list: tuple[str, ...]
    _seg_pop_arrays: dict[tuple[str, str], tuple[np.ndarray, np.ndarray, np.ndarray]]
    _variant_matrix: sparse.csr_matrix
    _cooc_matrix: sparse.csr_matrix
    _latent_index: LatentContentIndex
    _feat_ctx: V2FeatureContext
    _booster: Any
    _retrieval_config: RetrievalConfig

    def eligible(self, filters: Mapping[str, str]) -> frozenset[str]:
        eligible = self.catalog
        for key, value in filters.items():
            if key not in FILTERS or not isinstance(value, str):
                raise ValueError('Unknown filter')
            eligible = eligible & self.filter_index[key].get(value, frozenset())
        return eligible

    def _resolve_seg_key(self, age_bin: str, pref_ig: str) -> tuple[str, str]:
        if pref_ig:
            k1 = (age_bin, pref_ig)
            if k1 in self._seg_pop_arrays:
                return k1
            k2 = (_ALL_SEG, pref_ig)
            if k2 in self._seg_pop_arrays:
                return k2
        k3 = (age_bin, _ALL_SEG)
        if k3 in self._seg_pop_arrays:
            return k3
        return (_ALL_SEG, _ALL_SEG)

    def _user_base_history(self, u_i: int | None) -> tuple[np.ndarray, np.ndarray]:
        if u_i is None or self._feat_ctx._hist_keys.size == 0:
            return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)
        shift = self._feat_ctx._shift
        keys = self._feat_ctx._hist_keys
        lo = int(np.searchsorted(keys, np.int64(u_i) << shift, side='left'))
        hi = int(np.searchsorted(keys, np.int64(u_i + 1) << shift, side='left'))
        if hi <= lo:
            return np.empty(0, dtype=np.int32), np.empty(0, dtype=np.float32)
        mask = (np.int64(1) << shift) - 1
        nodes = (keys[lo:hi] & mask).astype(np.int32, copy=False)
        decays = self._feat_ctx._hist_decay[lo:hi]
        return nodes, decays

    def recommend(self, request: RecommendationRequest) -> RecommendationResponse:
        return self._recommend_impl(
            request,
            cutoff=self.data_as_of,
            popular=None,
            history=None,
            live_events=None,
        )

    def recommend_stream(
        self,
        request: RecommendationRequest,
        *,
        cutoff: date,
        popular: Sequence[str] | None = None,
        history: Sequence[str] | None = None,
        live_events: Sequence[Mapping[str, Any]] | Sequence[tuple[str, str]] | Mapping[str, float] | None = None,
    ) -> RecommendationResponse:
        return self._recommend_impl(
            request,
            cutoff=cutoff,
            popular=popular,
            history=history,
            live_events=live_events,
        )

    def _recommend_impl(
        self,
        request: RecommendationRequest,
        *,
        cutoff: date,
        popular: Sequence[str] | None,
        history: Sequence[str] | None,
        live_events: Sequence[Mapping[str, Any]] | Sequence[tuple[str, str]] | Mapping[str, float] | None,
    ) -> RecommendationResponse:
        if request.filters:
            eligible_articles = self.eligible(request.filters)
            if not eligible_articles:
                return RecommendationResponse(
                    request_id=str(uuid4()),
                    release_id=self.release_id,
                    strategy='two_stage_lgbm_v2',
                    features_timestamp=cutoff,
                    fallback=True,
                    items=[],
                )
            eligible_nodes: set[int] | None = {self._article_to_idx[a] for a in eligible_articles}
        else:
            eligible_nodes = None

        mode = getattr(request, 'mode', 'balanced')
        if mode not in ('balanced', 'discovery'):
            raise ValueError(f'Unsupported mode: {mode}')

        prefs_obj = getattr(request, 'preferences', None)
        pref_dict: dict[str, str] | None = None
        if prefs_obj is not None:
            if isinstance(prefs_obj, BaseModel):
                p_age_bin = getattr(prefs_obj, 'age_bin', None)
                p_ig = getattr(prefs_obj, 'index_group_name', None)
            else:
                p_age_bin = prefs_obj.get('age_bin')
                p_ig = prefs_obj.get('index_group_name')
            tmp_pref: dict[str, str] = {}
            if p_age_bin:
                tmp_pref['age_bin'] = str(p_age_bin)
            if p_ig:
                tmp_pref['index_group_name'] = str(p_ig)
            if tmp_pref:
                pref_dict = tmp_pref

        u_i = self._feat_ctx._user_to_idx.get(request.customer_id)
        base_nodes, base_decays = self._user_base_history(u_i)

        purchased_nodes: set[int] = set()
        purchase_weights: dict[int, float] = {}
        all_weights: dict[int, float] = {}
        base_hist_aids: set[str] = set()

        for node_arr, dec_val in zip(base_nodes, base_decays):
            node_i = int(node_arr)
            w_f = float(dec_val)
            purchased_nodes.add(node_i)
            base_hist_aids.add(self._article_ids[node_i])
            if w_f > 0.0:
                purchase_weights[node_i] = purchase_weights.get(node_i, 0.0) + w_f
                all_weights[node_i] = all_weights.get(node_i, 0.0) + w_f

        if history:
            for aid in history:
                node_i = self._article_to_idx.get(str(aid))
                if node_i is not None:
                    purchased_nodes.add(node_i)
                    if node_i not in purchase_weights:
                        purchase_weights[node_i] = 1.0
                        all_weights[node_i] = max(all_weights.get(node_i, 0.0), 1.0)

        direct_live_weights: dict[int, float] = {}
        parsed_live_for_features: list[tuple[str, str, float]] = []
        live_purchase_aids: set[str] = set()

        if live_events is not None:
            if isinstance(live_events, Mapping):
                for raw_aid, raw_w in live_events.items():
                    art_id = str(raw_aid)
                    w_f = float(raw_w)
                    node_i = self._article_to_idx.get(art_id)
                    if node_i is None or not math.isfinite(w_f) or w_f <= 0.0:
                        continue
                    if node_i in purchased_nodes or w_f >= 1.0:
                        kind = 'purchase'
                    elif w_f >= 0.5:
                        kind = 'click'
                    else:
                        kind = 'view'
                    mult = w_f / EVENT_KIND_WEIGHTS[kind]
                    direct_live_weights[node_i] = direct_live_weights.get(node_i, 0.0) + w_f
                    all_weights[node_i] = all_weights.get(node_i, 0.0) + w_f
                    if kind == 'purchase':
                        purchased_nodes.add(node_i)
                        purchase_weights[node_i] = purchase_weights.get(node_i, 0.0) + w_f
                        live_purchase_aids.add(art_id)
                    parsed_live_for_features.append((art_id, kind, mult))
            else:
                for ev in live_events:
                    if isinstance(ev, Mapping):
                        ev_date = _event_utc_date(ev.get('event_time'))
                        if ev_date is not None and ev_date >= cutoff:
                            continue
                        art_id = str(ev.get('article_id', ''))
                        kind = str(ev.get('kind', ''))
                        raw_w = ev.get('weight')
                        mult = 1.0 if raw_w is None else float(raw_w)
                    else:
                        art_id = str(ev[0])
                        kind = str(ev[1])
                        mult = 1.0
                    if kind not in EVENT_KIND_WEIGHTS:
                        raise ValueError(f'Unknown live event kind: {kind}')
                    w_f = EVENT_KIND_WEIGHTS[kind] * mult
                    node_i = self._article_to_idx.get(art_id)
                    if node_i is not None and math.isfinite(w_f) and w_f > 0.0:
                        direct_live_weights[node_i] = direct_live_weights.get(node_i, 0.0) + w_f
                        all_weights[node_i] = all_weights.get(node_i, 0.0) + w_f
                        if kind == 'purchase':
                            purchased_nodes.add(node_i)
                            purchase_weights[node_i] = purchase_weights.get(node_i, 0.0) + w_f
                            live_purchase_aids.add(art_id)
                        parsed_live_for_features.append((art_id, kind, mult))

        if history:
            for aid in history:
                aid_str = str(aid)
                if aid_str not in base_hist_aids and aid_str not in live_purchase_aids and aid_str in self._article_to_idx:
                    parsed_live_for_features.append((aid_str, 'purchase', 1.0))

        cfg = self._retrieval_config
        sentinel = cfg.missing_rank_sentinel
        pool: dict[int, tuple[list[float], list[int]]] = {}

        def _ensure_node(node_i: int) -> tuple[list[float], list[int]]:
            entry = pool.get(node_i)
            if entry is None:
                entry = ([0.0] * 7, [sentinel] * 7)
                pool[node_i] = entry
            return entry

        # 1. history_decay (purchases)
        if purchase_weights:
            p_nodes = np.fromiter(purchase_weights.keys(), dtype=np.int32)
            p_vals = np.fromiter(purchase_weights.values(), dtype=np.float32)
            order = np.lexsort((p_nodes, -p_vals))[: cfg.history_limit]
            for rank_i, pos_i in enumerate(order, start=1):
                sc_list, rk_list = _ensure_node(int(p_nodes[pos_i]))
                sc_list[0] = float(p_vals[pos_i])
                rk_list[0] = rank_i

        # 2-4. Personalized expansion from combined purchase + click + view weights
        if all_weights:
            all_nodes = np.fromiter(all_weights.keys(), dtype=np.int32)
            all_vals = np.fromiter(all_weights.values(), dtype=np.float32)
            seed_csr = sparse.csr_matrix(
                (all_vals, (np.zeros(all_nodes.size, dtype=np.int32), all_nodes)),
                shape=(1, len(self._article_ids)),
                dtype=np.float32,
            )
            purchased_arr = np.fromiter(purchased_nodes, dtype=np.int32) if purchased_nodes else None

            # 2. product_code_variants
            if self._variant_matrix.nnz > 0:
                var_row = (seed_csr @ self._variant_matrix).tocsr()
                if var_row.nnz > 0:
                    cols = var_row.indices
                    vals = var_row.data
                    mask = vals > 0.0
                    if purchased_arr is not None and purchased_arr.size:
                        mask = mask & (~np.isin(cols, purchased_arr, assume_unique=False))
                    cols = cols[mask]
                    vals = vals[mask]
                    if cols.size:
                        order = np.lexsort((cols, -vals))[: cfg.variant_limit]
                        for rank_i, pos_i in enumerate(order, start=1):
                            sc_list, rk_list = _ensure_node(int(cols[pos_i]))
                            sc_list[1] = float(vals[pos_i])
                            rk_list[1] = rank_i

            # 3. cooccurrence
            if self._cooc_matrix.nnz > 0:
                seed_norm = _row_l1_normalize(seed_csr)
                cooc_row = (seed_norm @ self._cooc_matrix).tocsr()
                if cooc_row.nnz > 0:
                    cols = cooc_row.indices
                    vals = cooc_row.data
                    pos_mask = vals > 0.0
                    if not np.all(pos_mask):
                        cols = cols[pos_mask]
                        vals = vals[pos_mask]
                    if cols.size:
                        order = np.lexsort((cols, -vals))[: cfg.cooc_limit]
                        for rank_i, pos_i in enumerate(order, start=1):
                            sc_list, rk_list = _ensure_node(int(cols[pos_i]))
                            sc_list[2] = float(vals[pos_i])
                            rk_list[2] = rank_i

            # 4a. latent SVD fold-in + query
            if self._latent_index.k > 0:
                u_vec = self._latent_index.fold_in_user(all_nodes, all_vals)
                l_idx, l_sc = self._latent_index.query_latent(
                    u_vec,
                    top_k=cfg.latent_limit,
                    exclude_indices=purchased_arr,
                )
                for rank_i, (ni, sc) in enumerate(zip(l_idx, l_sc), start=1):
                    sc_list, rk_list = _ensure_node(int(ni))
                    sc_list[3] = float(sc)
                    rk_list[3] = rank_i

            # 4b. content similarity
            if self._latent_index.vocab_size > 0:
                c_idx, c_sc = self._latent_index.query_content(
                    all_nodes,
                    all_vals,
                    top_k=cfg.content_limit,
                    exclude_indices=purchased_arr,
                )
                for rank_i, (ni, sc) in enumerate(zip(c_idx, c_sc), start=1):
                    sc_list, rk_list = _ensure_node(int(ni))
                    sc_list[4] = float(sc)
                    rk_list[4] = rank_i

        # Ensure direct live_events items enter Stage-2 candidate pool
        for node_i in direct_live_weights:
            if mode == 'discovery' and node_i in purchased_nodes:
                continue
            _ensure_node(node_i)

        # 5a. segment_popularity (only when real age_bin or index_group_name is known)
        age_b = 'unknown'
        pref_ig = ''
        if u_i is not None:
            age_c = int(self._user_age_bin_codes[u_i])
            ig_c = int(self._user_pref_ig_codes[u_i])
            age_b = AGE_BINS[age_c] if age_c < len(AGE_BINS) else 'unknown'
            pref_ig = self._ig_list[ig_c] if ig_c < len(self._ig_list) else ''
        if pref_dict is not None:
            if 'age_bin' in pref_dict:
                cand_ab = pref_dict['age_bin']
                age_b = cand_ab if cand_ab in _AGE_BIN_TO_CODE else 'unknown'
            if 'index_group_name' in pref_dict:
                pref_ig = pref_dict['index_group_name']

        has_real_segment = (age_b != 'unknown') or (pref_ig != '')
        if has_real_segment and self._seg_pop_arrays:
            seg_key = self._resolve_seg_key(age_b, pref_ig)
            seg_entry = self._seg_pop_arrays.get(seg_key)
            if seg_entry is not None:
                seg_nodes, seg_scores, seg_ranks = seg_entry
                for ni, sc, rk in zip(
                    seg_nodes[: cfg.segment_pop_limit],
                    seg_scores[: cfg.segment_pop_limit],
                    seg_ranks[: cfg.segment_pop_limit],
                ):
                    sc_list, rk_list = _ensure_node(int(ni))
                    sc_list[5] = float(sc)
                    rk_list[5] = int(rk)

        # 5b. global pop7d
        for ni, sc, rk in zip(
            self._pop7d_nodes[: cfg.global_pop_limit],
            self._pop7d_scores[: cfg.global_pop_limit],
            self._pop7d_ranks[: cfg.global_pop_limit],
        ):
            sc_list, rk_list = _ensure_node(int(ni))
            sc_list[6] = float(sc)
            rk_list[6] = int(rk)

        # Apply request filters & discovery purchase exclusion before ranking/truncation
        cand_nodes: list[int] = []
        for node_i in pool:
            if eligible_nodes is not None and node_i not in eligible_nodes:
                continue
            if mode == 'discovery' and node_i in purchased_nodes:
                continue
            cand_nodes.append(node_i)

        # Top up from full catalog popularity if narrow filters or discovery exhaustion reduced pool below k
        if len(cand_nodes) < request.k:
            cand_set = set(cand_nodes)
            target_pool = max(request.k * 3, 24) if mode == 'discovery' else request.k
            for rank_pos, raw_ni in enumerate(self._global_pop_order, start=1):
                ni = int(raw_ni)
                if ni in cand_set:
                    continue
                if eligible_nodes is not None and ni not in eligible_nodes:
                    continue
                if mode == 'discovery' and ni in purchased_nodes:
                    continue
                sc_list, rk_list = _ensure_node(ni)
                if rk_list[6] >= sentinel:
                    sc_list[6] = float(math.log1p(float(self._item_count7[ni])))
                    rk_list[6] = min(rank_pos, sentinel - 1)
                cand_nodes.append(ni)
                cand_set.add(ni)
                if len(cand_nodes) >= target_pool:
                    break

        if not cand_nodes:
            return RecommendationResponse(
                request_id=str(uuid4()),
                release_id=self.release_id,
                strategy='two_stage_lgbm_v2',
                features_timestamp=cutoff,
                fallback=True,
                items=[],
            )

        n_cands = len(cand_nodes)
        cand_article_ids = [self._article_ids[ni] for ni in cand_nodes]
        cand_data: dict[str, pl.Series] = {
            'customer_id': pl.Series('customer_id', [request.customer_id] * n_cands, dtype=pl.String),
            'article_id': pl.Series('article_id', cand_article_ids, dtype=pl.String),
        }
        for idx, col in enumerate(SCORE_COLUMNS):
            cand_data[col] = pl.Series(col, [pool[ni][0][idx] for ni in cand_nodes], dtype=pl.Float32)
        for idx, col in enumerate(RANK_COLUMNS):
            cand_data[col] = pl.Series(col, [pool[ni][1][idx] for ni in cand_nodes], dtype=pl.Int16)
        candidates_df = pl.DataFrame(cand_data)

        X, is_cold_start = self._feat_ctx.transform_online(
            candidates_df,
            customer_id=request.customer_id,
            live_events=parsed_live_for_features if parsed_live_for_features else None,
            preferences=pref_dict,
        )
        rank_seg_arr = np.fromiter((pool[ni][1][5] for ni in cand_nodes), dtype=np.float64, count=n_cands)
        rank_pop_arr = np.fromiter((pool[ni][1][6] for ni in cand_nodes), dtype=np.float64, count=n_cands)
        scores = predict_scores(
            X,
            booster=self._booster,
            is_cold_start=is_cold_start,
            rank_seg_pop=rank_seg_arr,
            rank_pop7d=rank_pop_arr,
            num_threads=1,
        )

        # Calibrated live-session signal bonus (0 effect on /recommend when direct_live_weights is empty)
        if direct_live_weights:
            spread = max(1.0, float(scores.max() - scores.min())) if scores.size else 1.0
            for idx, node_i in enumerate(cand_nodes):
                lw = direct_live_weights.get(node_i)
                if lw is not None and lw > 0.0:
                    scores[idx] += (spread + 1.0) * (1.0 + 4.0 * lw)

        order = sorted(
            range(n_cands),
            key=lambda idx: (-float(scores[idx]), cand_article_ids[idx]),
        )

        selected_indices: list[int] = []
        if mode == 'discovery':
            seen_pcodes: set[str] = set()
            for idx in order:
                ni = cand_nodes[idx]
                pcode = self._product_codes[ni]
                if pcode:
                    if pcode in seen_pcodes:
                        continue
                    seen_pcodes.add(pcode)
                selected_indices.append(idx)
                if len(selected_indices) >= request.k:
                    break
        else:
            selected_indices = order[: request.k]

        items: list[RecommendationItem] = []
        for rank_out, idx in enumerate(selected_indices, start=1):
            ni = cand_nodes[idx]
            aid = cand_article_ids[idx]
            rk_list = pool[ni][1]
            reason = self._select_reason(
                ni,
                rk_list,
                sentinel=sentinel,
                purchased_nodes=purchased_nodes,
                direct_live_weights=direct_live_weights,
                has_real_segment=has_real_segment,
            )
            items.append(RecommendationItem(article_id=aid, rank=rank_out, reason=reason))  # type: ignore[arg-type]

        return RecommendationResponse(
            request_id=str(uuid4()),
            release_id=self.release_id,
            strategy='two_stage_lgbm_v2',
            features_timestamp=cutoff,
            fallback=bool(is_cold_start),
            items=items,
        )

    @staticmethod
    def _select_reason(
        node_i: int,
        rk_list: list[int],
        *,
        sentinel: int,
        purchased_nodes: set[int],
        direct_live_weights: dict[int, float],
        has_real_segment: bool,
    ) -> str:
        for rk_idx, (_, reason_tag) in enumerate(REASON_PRIORITY):
            if rk_list[rk_idx] >= sentinel:
                continue
            if reason_tag == 'recent_purchase' and node_i not in purchased_nodes:
                continue
            if reason_tag == 'popular_for_segment' and not has_real_segment:
                continue
            return reason_tag
        if node_i in purchased_nodes:
            return 'recent_purchase'
        if node_i in direct_live_weights:
            return 'content_similar'
        return 'popular_7d'


def load_state(directory: Path | str) -> TwoStageState:
    root = Path(directory)
    manifest = verify_v2_release(root)
    retrieval_config = RetrievalConfig(**manifest.rules.retrieval_config)

    catalog = pl.read_parquet(root / 'catalog.parquet')
    item_stats = pl.read_parquet(root / 'item_stats.parquet')

    n_items = catalog.height
    article_ids = tuple(catalog['article_id'].to_list())
    article_to_idx = {aid: i for i, aid in enumerate(article_ids)}
    product_codes = tuple(catalog['product_code'].fill_null('').to_list())
    index_groups_cat = catalog['index_group_name'].fill_null('').to_list()

    item_count7 = item_stats['item_count7'].to_numpy().astype(np.int64, copy=False)
    item_count14 = item_stats['item_count14'].to_numpy().astype(np.float32, copy=False)
    global_pop_order = np.lexsort((np.arange(n_items, dtype=np.int32), -item_count7)).astype(np.int32)
    pop7d_limit = min(retrieval_config.global_pop_limit, n_items)
    pop7d_nodes = global_pop_order[:pop7d_limit].astype(np.int32, copy=False)
    pop7d_scores = np.log1p(item_count7[pop7d_nodes].astype(np.float32)).astype(np.float32, copy=False)
    pop7d_ranks = np.arange(1, pop7d_limit + 1, dtype=np.int16)
    popularity = tuple(article_ids[int(i)] for i in global_pop_order)

    variant_matrix = _build_variant_matrix_from_catalog(
        product_codes,
        item_count14,
        retrieval_config.variant_per_code_cap,
    )

    filter_indexes: dict[str, MappingProxyType] = {}
    for field in FILTERS:
        groups: dict[str, set[str]] = {}
        for item, value in catalog.select('article_id', field).iter_rows():
            if value is not None:
                groups.setdefault(str(value), set()).add(str(item))
        filter_indexes[field] = MappingProxyType({key: frozenset(values) for key, values in groups.items()})

    user_stats = pl.read_parquet(root / 'user_stats.parquet')
    customers = frozenset(user_stats['customer_id'].to_list())

    distinct_igs = sorted(
        {str(ig) for ig in index_groups_cat if ig}
        | {str(ig) for ig in user_stats['pref_index_group'].fill_null('').to_list() if ig}
    )
    ig_list = ('',) + tuple(distinct_igs)
    ig_to_code = {name: i for i, name in enumerate(ig_list)}
    ig_dtype = np.uint8 if len(ig_list) <= 255 else np.uint16

    user_age_bin_codes = np.fromiter(
        (_AGE_BIN_TO_CODE.get(ab, _UNKNOWN_AGE_CODE) for ab in user_stats['age_bin'].fill_null('unknown').to_list()),
        dtype=np.uint8,
        count=user_stats.height,
    )
    user_pref_ig_codes = np.fromiter(
        (ig_to_code.get(ig, 0) for ig in user_stats['pref_index_group'].fill_null('').to_list()),
        dtype=ig_dtype,
        count=user_stats.height,
    )
    del index_groups_cat

    user_affinities = pl.read_parquet(root / 'user_affinities.parquet')
    histories = pl.read_parquet(root / 'histories.parquet')
    feat_ctx = V2FeatureContext.from_tables(
        catalog=catalog,
        item_stats=item_stats,
        user_stats=user_stats,
        user_affinities=user_affinities,
        histories=histories,
        cutoff=manifest.data_as_of,
        retain_dataframes=False,
    )
    del catalog, item_stats, user_stats, user_affinities, histories
    gc.collect()

    seg_pop_df = pl.read_parquet(root / 'segment_popularity.parquet')
    seg_pop_arrays: dict[tuple[str, str], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    if not seg_pop_df.is_empty():
        seg_pop_sorted = seg_pop_df.sort(['age_bin', 'index_group_name', 'rank_seg_pop', 'node'])
        for (age_b, ig_n), part in seg_pop_sorted.partition_by(
            ['age_bin', 'index_group_name'], as_dict=True, maintain_order=True
        ).items():
            seg_pop_arrays[(str(age_b), str(ig_n))] = (
                part['node'].to_numpy().astype(np.int32, copy=False),
                part['score_seg_pop'].to_numpy().astype(np.float32, copy=False),
                part['rank_seg_pop'].to_numpy().astype(np.int16, copy=False),
            )
    del seg_pop_df

    cooc_matrix = sparse.load_npz(root / 'cooc.npz').tocsr().astype(np.float32, copy=False)
    latent_index = LatentContentIndex.load(root, article_ids=article_ids)
    booster = load_booster(root / 'model.txt', expected_feature_names=manifest.rules.feature_names)

    state = TwoStageState(
        release_id=manifest.release_id,
        data_as_of=manifest.data_as_of,
        customers=customers,
        catalog=frozenset(article_ids),
        popularity=popularity,
        filter_index=MappingProxyType(filter_indexes),
        _article_ids=article_ids,
        _article_to_idx=article_to_idx,
        _product_codes=product_codes,
        _item_count7=item_count7,
        _global_pop_order=global_pop_order,
        _pop7d_nodes=pop7d_nodes,
        _pop7d_scores=pop7d_scores,
        _pop7d_ranks=pop7d_ranks,
        _user_age_bin_codes=user_age_bin_codes,
        _user_pref_ig_codes=user_pref_ig_codes,
        _ig_list=ig_list,
        _seg_pop_arrays=seg_pop_arrays,
        _variant_matrix=variant_matrix,
        _cooc_matrix=cooc_matrix,
        _latent_index=latent_index,
        _feat_ctx=feat_ctx,
        _booster=booster,
        _retrieval_config=retrieval_config,
    )
    if not state.recommend(RecommendationRequest(customer_id='__smoke__', k=1)).items:
        raise ValueError('Release cannot recommend')
    return state


load_v2_state = load_state

__all__ = ['FILTERS', 'TwoStageState', 'load_state', 'load_v2_state']
