# Synthetic Multi-Robot Log Generator

This package generates reproducible synthetic execution logs for the quantitative evaluation of OC-M3. The generator is an **evaluation instrument**, not a contribution of the paper: allocation strategies are treatments used to induce different organizational/collaboration behavior, while OC-M3 is evaluated on whether it can correctly instantiate and characterize that behavior.

## Evaluation design

The complete experiment is a factorial design:

- **Scenarios:** `agriculture`, `cleaning`, `order_management`
- **Allocation treatments:** `random`, `greedy`, `hungarian`, `auction`
- **Repeated seeds:** `1..10` by default in `run_all.py`

For a fixed scenario and seed, every allocation strategy receives the same robot profiles, robot starting positions, mission/segment structure, task locations, task releases, and task/control duration draws. Task and control durations use common random numbers keyed by logical task/control identity, so allocator comparisons are not confounded by different stochastic duration streams. Travel, waiting, recharge occurrence, and resulting timestamps can still differ because they are endogenous consequences of the allocation.

## Mission and segment semantics

A mission may contain both:

1. **Mission-level tasks**, correlated with a mission but no segment (`segment_id` empty).
2. **Segment-level tasks**, correlated with both a mission and one of its segments.

Segments explicitly belong to one mission. Each generated mission has a mission-level prefix, multiple segment branches, and a mission-level suffix. Segment branches may overlap after their common prefix and synchronize before the suffix.

Control events are robot-local by default. Use `--control-context` only if you intentionally want mission/segment IDs copied onto Control events.

## Allocation treatments

- `random`: random feasible assignment baseline.
- `greedy`: greedy nearest/earliest-feasible assignment.
- `hungarian`: centralized minimum-cost bipartite assignment using the Hungarian/Kuhn-Munkres algorithm (`scipy.optimize.linear_sum_assignment`).
- `auction`: market/Contract-Net-inspired minimum-bid allocation with travel, waiting, and mission-switch penalties.

These strategies are **not claimed as novel implementations**. In the paper, cite the underlying allocation paradigms/algorithms rather than presenting the software implementation as a contribution. For example, the Hungarian treatment can be grounded in Kuhn (1955), while the auction/Contract-Net family can be grounded in Smith (1980) and established market-based multi-robot task-allocation literature.

## Run one case

```bash
python generate.py \
  --scenario order_management \
  --allocation auction \
  --robots 8 \
  --missions 20 \
  --segments-per-mission 3 \
  --arrival-interval 10 \
  --seed 42 \
  --start-time "2026-01-01T08:00:00" \
  --out output/order_management_auction_seed42
```

Exported timestamps have centisecond precision in ISO-like form `YYYY-MM-DDTHH:MM:SS.ff`.

## Run the complete experiment

```bash
python run_all.py
```

The default is 3 scenarios × 4 allocation strategies × 10 seeds = **120 runs**.
For a smaller test:

```bash
python run_all.py --seeds 1 2 --missions 5
```

The experiment root also contains:

- `experiment_config.json`: complete experiment parameters.
- `experiment_summary.csv`: one row per run with event counts and latent ground-truth pattern counts.

## Files produced by each run

### OC-M3 input

- `events.csv`: Task and Control events with interval timestamps.
- `task_requirements.csv`: normalized `task_name, required_capability` table.
- `task_table.csv`: loader-compatible alias of the same requirement table.
- `robots.csv`: normalized robot-capability relation.
- `robot_table.csv`: one row per robot with semicolon-separated capabilities, ready for the EKG loader.
- `missions.csv`: mission entities and release times.
- `segments.csv`: segment entities and parent mission.
- `tasks.csv`: generated task instances and scope (`mission` or `segment`).
- `precedence.csv`: task precedence edges.
- `ekg_loader_config.json`: ready-to-use configuration for `query-lib/init_ekg.py` in the OC-M3 analysis repository.
- `config.json`: generation parameters and event counts.

### Latent ground truth

Ground truth is computed directly from simulator state/events, not by querying the generated EKG. Its temporal directly-follows relation reproduces the interval semantics used by `init_ekg.py`.

- `ground_truth_assignments.csv`: latent task-to-robot assignments.
- `ground_truth_df.csv`: Mission, Segment, and Robot Task-DF relations.
- `ground_truth_patterns.csv`: generic union table for inspection.
- `ground_truth_summary.csv`: counts by structure and perspective.

The following files use the exact schemas expected by `query-lib/evaluate_pattern_correctness.py`:

- `handover_occurrences.csv`
- `switch_occurrences.csv`
- `capability_return_occurrences.csv`
- `parallel_occurrences.csv`

They correspond to the four formal structures evaluated in OC-M3:

1. robot handover;
2. objective switch;
3. capability-driven return;
4. parallel collaboration.

The handover ground truth includes the same overlap guards as the paper/query implementation; objective switches are derived over robot-perspective Task-DF; capability returns use objective-perspective DF chains and capability unavailability; parallel collaboration follows the same mission/segment envelope and team semantics as the OC-M3 queries.

## Interpreting seeds

A seed identifies one workload realization. Comparing algorithms **within the same seed** controls exogenous scenario randomness. Comparing results over multiple seeds yields distributions instead of relying on a single synthetic execution.
