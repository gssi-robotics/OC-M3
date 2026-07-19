"""
Cypher queries for extracting collaboration-pattern occurrences for adaptive multi-robot mission execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional


@dataclass(frozen=True)
class Schema:
    """ Adopted EKG-based data model for the collaboration patterns """
  
    event_label: str = "Event"
    entity_label: str = "Entity"
    capability_label: Optional[str] = "Capability"

    corr_rel: str = "CORR"
    df_rel: str = "DF"
    part_of_rel: str = "PART_OF"
    has_rel: str = "HAS"
    req_rel: str = "REQ"

    entity_id_prop: str = "id"
    entity_type_prop: str = "type"

    event_id_prop: str = "event_id"
    event_activity_prop: str = "activity"
    event_start_prop: str = "start"
    event_end_prop: str = "end"

    df_perspective_id_prop: str = "perspective_id"
    df_perspective_type_prop: str = "perspective_type"
    df_transition_prop: str = "transitionTimeSeconds"

    def node(self, var: str, label: Optional[str]) -> str:
        if label is None or label == "":
            return f"({var})"
        return f"({var}:{label})"

    def event(self, var: str) -> str:
        return self.node(var, self.event_label)

    def entity(self, var: str) -> str:
        return self.node(var, self.entity_label)

    def capability(self, var: str) -> str:
        return self.node(var, self.capability_label)

    def type_filter(self, var: str, value: str) -> str:
        return f"{var}.{self.entity_type_prop} = '{value}'"

    def rel(self, rel_type: str, var: Optional[str] = None) -> str:
        if var:
            return f"[{var}:{rel_type}]"
        return f"[:{rel_type}]"

    def df_perspective_predicate(self, df_var: str, entity_var: str, perspective_type: str) -> str:
        """Return a Cypher predicate for matching a DF edge with a given perspective type and ID."""
        return (
            f"{df_var}.{self.df_perspective_type_prop} = '{perspective_type}' "
            f"AND toString({df_var}.{self.df_perspective_id_prop}) = toString({entity_var}.{self.entity_id_prop})"
        )

    def seconds_between(self, start_expr: str, end_expr: str) -> str:
        """Return a Cypher expression for seconds between two datetime expressions."""
        return f"duration.inSeconds({start_expr}, {end_expr}).seconds"

    def df_transition_expr(self, df_var: str, e_i: str, e_j: str) -> str:
        """Transition time on a DF edge, with fallback to event timestamps."""
        return (
            f"coalesce({df_var}.{self.df_transition_prop}, "
            f"{self.seconds_between(f'{e_i}.{self.event_end_prop}', f'{e_j}.{self.event_start_prop}')})"
        )


class CollaborationPatternCypher:
    """Cypher queries implementing collaboration patterns."""

    def __init__(self, schema: Schema = Schema()) -> None:
        self.s = schema

    # ------------------------------------------------------------------
    # Pattern occurrence queries: I_p(G)
    # ------------------------------------------------------------------

    def robot_handover(self, objective_type: str = "Mission") -> str:
        """Robot handover over Mission or Segment perspective.

        Returns tuples: <e_i, e_j, objective, fromRobot, toRobot>.
        """
        s = self.s
        pred = s.df_perspective_predicate("df", "o", objective_type)
        transition_expr = s.df_transition_expr("df", "e_i", "e_j")
        return f"""
            MATCH {s.event('e_i')}-{s.rel(s.df_rel, 'df')}->{s.event('e_j')}
            MATCH {s.event('e_i')}-{s.rel(s.corr_rel)}->{s.entity('o')}<-{s.rel(s.corr_rel)}-{s.event('e_j')}
            MATCH {s.event('e_i')}-{s.rel(s.corr_rel)}->{s.entity('ro_i')}
            MATCH {s.event('e_j')}-{s.rel(s.corr_rel)}->{s.entity('ro_j')}
            WHERE {s.type_filter('o', objective_type)} AND {s.type_filter('ro_i', 'Robot')}
              AND {s.type_filter('ro_j', 'Robot')} AND {pred} AND ro_i <> ro_j
            WITH e_i, e_j, o, ro_i, ro_j, df, {transition_expr} AS transitionTime
            RETURN e_i, e_j, o AS objective, ro_i AS fromRobot, ro_j AS toRobot,
              transitionTime, e_i.{s.event_activity_prop} AS fromActivity, e_j.{s.event_activity_prop} AS toActivity
            ORDER BY objective.{s.entity_id_prop}, e_i.{s.event_start_prop}
            """.strip()

    def co_participation(self, objective_type: str = "Mission") -> str:
        """Co-participation as one occurrence per objective with its unique team.

        Returns tuples: <objective, team>, with team size, objective duration,
        and event-distribution metrics. This avoids generating all pairwise
        robot combinations.
        """
        s = self.s
        objective_duration = s.seconds_between("objectiveStart", "objectiveEnd")
        return f"""
              MATCH {s.entity('o')}<-{s.rel(s.corr_rel)}-{s.event('e')}
              WHERE {s.type_filter('o', objective_type)}
              WITH o, collect(DISTINCT e) AS objectiveEvents, min(e.{s.event_start_prop}) AS objectiveStart, max(e.{s.event_end_prop}) AS objectiveEnd
              MATCH {s.entity('o')}<-{s.rel(s.corr_rel)}-{s.event('e_by_robot')}-{s.rel(s.corr_rel)}->{s.entity('ro')}
              WHERE {s.type_filter('ro', 'Robot')}
              WITH o, objectiveEvents, objectiveStart, objectiveEnd, ro, count(DISTINCT e_by_robot) AS eventsByRobot
              WITH o, objectiveEvents, objectiveStart, objectiveEnd, collect(ro) AS team,
                collect({{robot: ro, events: eventsByRobot}}) AS robotEventStats,
                count(ro) AS teamSize, max(eventsByRobot) AS maxEventsPerRobot
              WHERE teamSize > 1
              RETURN o AS objective, team, teamSize, {objective_duration} AS objectiveDuration,
                toFloat(size(objectiveEvents)) / teamSize AS avgEventsPerRobot, maxEventsPerRobot, robotEventStats
              ORDER BY objective.{s.entity_id_prop}
              """.strip()

    def objective_switch(self, objective_type: str = "Mission") -> str:
        """Objective switch over the robot perspective.

        Returns tuples: <e_i, e_j, robot, fromObjective, toObjective>.
        """
        s = self.s
        pred = s.df_perspective_predicate("df", "ro", "Robot")
        transition_expr = s.df_transition_expr("df", "e_i", "e_j")
        return f"""
                MATCH {s.event('e_i')}-{s.rel(s.df_rel, 'df')}->{s.event('e_j')}
                MATCH {s.event('e_i')}-{s.rel(s.corr_rel)}->{s.entity('ro')}<-{s.rel(s.corr_rel)}-{s.event('e_j')}
                MATCH {s.event('e_i')}-{s.rel(s.corr_rel)}->{s.entity('o_i')}
                MATCH {s.event('e_j')}-{s.rel(s.corr_rel)}->{s.entity('o_j')}
                WHERE {s.type_filter('ro', 'Robot')}
                  AND {s.type_filter('o_i', objective_type)}
                  AND {s.type_filter('o_j', objective_type)}
                  AND {pred}
                  AND o_i <> o_j
                WITH e_i, e_j, ro, o_i, o_j, df, {transition_expr} AS switchTime
                RETURN e_i, e_j, ro AS robot, o_i AS fromObjective, o_j AS toObjective, switchTime,
                  e_i.{s.event_activity_prop} AS fromActivity, e_j.{s.event_activity_prop} AS toActivity
                ORDER BY robot.{s.entity_id_prop}, e_i.{s.event_start_prop}
                """.strip()

    def capability_driven_return(self, objective_type: str = "Mission") -> str:
        """Capability-driven return over Mission or Segment perspective.

        Returns tuples: <e_i, e_j, e_k, objective, returningRobot, intermediateRobot, capabilities>.
        """
        s = self.s
        pred1 = s.df_perspective_predicate("df_ij", "o", objective_type)
        pred2 = s.df_perspective_predicate("df_jk", "o", objective_type)
        transition_to_intermediate = s.df_transition_expr("df_ij", "e_i", "e_j")
        transition_back = s.df_transition_expr("df_jk", "e_j", "e_k")
        intermediate_duration = s.seconds_between(f"e_j.{s.event_start_prop}", f"e_j.{s.event_end_prop}")
        return_time = f"transitionToIntermediate + intermediateDuration + transitionBack"
        return f"""
                MATCH {s.event('e_i')}-{s.rel(s.df_rel, 'df_ij')}->{s.event('e_j')}-{s.rel(s.df_rel, 'df_jk')}->{s.event('e_k')}
                MATCH {s.event('e_i')}-{s.rel(s.corr_rel)}->{s.entity('o')}<-{s.rel(s.corr_rel)}-{s.event('e_j')}
                MATCH {s.event('e_k')}-{s.rel(s.corr_rel)}->(o)
                MATCH {s.event('e_i')}-{s.rel(s.corr_rel)}->{s.entity('ro_a')}<-{s.rel(s.corr_rel)}-{s.event('e_k')}
                MATCH {s.event('e_j')}-{s.rel(s.corr_rel)}->{s.entity('ro_b')}
                MATCH {s.event('e_j')}-{s.rel(s.req_rel)}->{s.capability('c')}<-{s.rel(s.has_rel)}-{s.entity('ro_b')}
                WHERE {s.type_filter('o', objective_type)} AND {s.type_filter('ro_a', 'Robot')}
                  AND {s.type_filter('ro_b', 'Robot')} AND {pred1} AND {pred2} AND ro_a <> ro_b
                  AND NOT (ro_a)-{s.rel(s.has_rel)}->(c)
                  AND NOT EXISTS {{
                    MATCH {s.event('e_j')}-{s.rel(s.req_rel)}->(c_req)
                    WHERE NOT (ro_b)-{s.rel(s.has_rel)}->(c_req)
                  }}
                WITH e_i, e_j, e_k, o, ro_a, ro_b, df_ij, df_jk,
                  collect(DISTINCT c) AS missingCapabilitiesForReturningRobot
                WHERE size(missingCapabilitiesForReturningRobot) > 0
                WITH e_i, e_j, e_k, o, ro_a, ro_b,
                  missingCapabilitiesForReturningRobot,
                  {transition_to_intermediate} AS transitionToIntermediate,
                  {transition_back} AS transitionBack,
                  {intermediate_duration} AS intermediateDuration
                RETURN e_i, e_j, e_k, o AS objective,
                  ro_a AS returningRobot, ro_b AS intermediateRobot,
                  missingCapabilitiesForReturningRobot AS capabilities,
                  transitionToIntermediate, transitionBack,
                  e_j.{s.event_activity_prop} AS intermediateActivity,
                  intermediateDuration,
                  {return_time} AS returnTime
                ORDER BY objective.{s.entity_id_prop}, e_i.{s.event_start_prop}
                """.strip()

    def parallel_collaboration_mission(self) -> str:
        """Parallel collaboration between mission instances."""
        s = self.s
        overlap_duration = s.seconds_between("overlapStart", "overlapEnd")
        return f"""
            MATCH {s.entity('m1')}, {s.entity('m2')}
            WHERE {s.type_filter('m1', 'Mission')}
              AND {s.type_filter('m2', 'Mission')}
              AND m1.{s.entity_id_prop} < m2.{s.entity_id_prop}
            MATCH {s.entity('m1')}<-{s.rel(s.corr_rel)}-{s.event('e1')}
            WITH m1, m2, min(e1.{s.event_start_prop}) AS start1, max(e1.{s.event_end_prop}) AS end1
            MATCH {s.entity('m2')}<-{s.rel(s.corr_rel)}-{s.event('e2')}
            WITH m1, m2, start1, end1, min(e2.{s.event_start_prop}) AS start2, max(e2.{s.event_end_prop}) AS end2
            WHERE start1 < end2 AND start2 < end1
            MATCH {s.entity('m1')}<-{s.rel(s.corr_rel)}-{s.event('ev1')}-{s.rel(s.corr_rel)}->{s.entity('r1')}
            WHERE {s.type_filter('r1', 'Robot')}
            WITH m1, m2, start1, end1, start2, end2, collect(DISTINCT r1) AS team1
            MATCH {s.entity('m2')}<-{s.rel(s.corr_rel)}-{s.event('ev2')}-{s.rel(s.corr_rel)}->{s.entity('r2')}
            WHERE {s.type_filter('r2', 'Robot')}
            WITH
              m1, m2, start1, end1, start2, end2, team1, collect(DISTINCT r2) AS team2
            WITH m1, m2, start1, end1, start2, end2, team1, team2,
              size(team1) + size([r IN team2 WHERE NOT r IN team1]) AS unionTeamSize,
              [r IN team1 WHERE r IN team2] AS sharedRobots,
              CASE WHEN start1 >= start2 THEN start1 ELSE start2 END AS overlapStart,
              CASE WHEN end1 <= end2 THEN end1 ELSE end2 END AS overlapEnd
            WHERE unionTeamSize > 1
            RETURN m1 AS mission1, m2 AS mission2, team1, team2, sharedRobots, 
              size(sharedRobots) AS robotCompetition, start1, end1,
              start2, end2, overlapStart, overlapEnd, {overlap_duration} AS overlapDuration
            ORDER BY mission1.{s.entity_id_prop}, mission2.{s.entity_id_prop}
            """.strip()

    def parallel_collaboration_segment(self) -> str:
        """Parallel collaboration between segment instances of the same mission."""
        s = self.s
        overlap_duration = s.seconds_between("overlapStart", "overlapEnd")
        return f"""
                MATCH {s.entity('s1')}-{s.rel(s.part_of_rel)}->{s.entity('m')}<-{s.rel(s.part_of_rel)}-{s.entity('s2')}
                WHERE {s.type_filter('s1', 'Segment')}
                  AND {s.type_filter('s2', 'Segment')}
                  AND {s.type_filter('m', 'Mission')}
                  AND s1.{s.entity_id_prop} < s2.{s.entity_id_prop}
                MATCH {s.entity('s1')}<-{s.rel(s.corr_rel)}-{s.event('e1')}
                WITH s1, s2, m, min(e1.{s.event_start_prop}) AS start1, max(e1.{s.event_end_prop}) AS end1
                MATCH {s.entity('s2')}<-{s.rel(s.corr_rel)}-{s.event('e2')}
                WITH s1, s2, m, start1, end1, min(e2.{s.event_start_prop}) AS start2, max(e2.{s.event_end_prop}) AS end2
                WHERE start1 < end2 AND start2 < end1
                MATCH {s.entity('s1')}<-{s.rel(s.corr_rel)}-{s.event('ev1')}-{s.rel(s.corr_rel)}->{s.entity('r1')}
                WHERE {s.type_filter('r1', 'Robot')}
                WITH s1, s2, m, start1, end1, start2, end2, collect(DISTINCT r1) AS team1
                MATCH {s.entity('s2')}<-{s.rel(s.corr_rel)}-{s.event('ev2')}-{s.rel(s.corr_rel)}->{s.entity('r2')}
                WHERE {s.type_filter('r2', 'Robot')}
                WITH s1, s2, m, start1, end1, start2, end2, team1, collect(DISTINCT r2) AS team2
                WITH s1, s2, m, start1, end1, start2, end2, team1, team2,
                  size(team1) + size([r IN team2 WHERE NOT r IN team1]) AS unionTeamSize,
                  [r IN team1 WHERE r IN team2] AS sharedRobots,
                  CASE WHEN start1 >= start2 THEN start1 ELSE start2 END AS overlapStart,
                  CASE WHEN end1 <= end2 THEN end1 ELSE end2 END AS overlapEnd
                WHERE unionTeamSize > 1
                RETURN s1 AS segment1, s2 AS segment2, m AS mission, 
                      team1, team2, sharedRobots, size(sharedRobots) AS robotCompetition,
                      start1, end1, start2, end2, overlapStart, overlapEnd, {overlap_duration} AS overlapDuration
                ORDER BY mission.{s.entity_id_prop}, segment1.{s.entity_id_prop}, segment2.{s.entity_id_prop}
                """.strip()

    def synchronization_diagnostics_parallel_segments(self) -> str:
        """Synchronization delay and branch waiting for parallel segment pairs.

        For each pair of parallel segments in the same mission, this query finds
        the first downstream mission-level event after both segments complete.
        Mission-level events are events correlated with the mission but not with
        any segment of that mission.
        """
        s = self.s
        occurrence_query = self.parallel_collaboration_segment()
        latest_end = "CASE WHEN end1 >= end2 THEN end1 ELSE end2 END"
        sync_delay = s.seconds_between("latestEnd", f"downstreamEvent.{s.event_start_prop}")
        branch_wait_1 = s.seconds_between("end1", f"downstreamEvent.{s.event_start_prop}")
        branch_wait_2 = s.seconds_between("end2", f"downstreamEvent.{s.event_start_prop}")
        return f"""
              CALL {{{_indent(occurrence_query, 2)}}}
              WITH mission, segment1, segment2, end1, end2, {latest_end} AS latestEnd
              MATCH {s.entity('mission')}<-{s.rel(s.corr_rel)}-{s.event('candidate')}
              WHERE candidate.{s.event_start_prop} >= latestEnd
                AND NOT EXISTS {{
                  MATCH {s.event('candidate')}-{s.rel(s.corr_rel)}->{s.entity('seg')}-{s.rel(s.part_of_rel)}->{s.entity('mission')}
                  WHERE {s.type_filter('seg', 'Segment')}
                }}
              WITH mission, segment1, segment2, end1, end2, latestEnd, candidate
              ORDER BY candidate.{s.event_start_prop} ASC
              WITH mission, segment1, segment2, end1, end2, latestEnd, head(collect(candidate)) AS downstreamEvent
              WHERE downstreamEvent IS NOT NULL
              RETURN mission, segment1, segment2, downstreamEvent, latestEnd, 
                    downstreamEvent.{s.event_start_prop} AS downstreamStart, {sync_delay} AS syncDelay,
                    {branch_wait_1} + {branch_wait_2} AS branchWait
              ORDER BY mission.{s.entity_id_prop}, segment1.{s.entity_id_prop}, segment2.{s.entity_id_prop}
              """.strip()

    # ------------------------------------------------------------------
    # Aggregated diagnostic queries
    # ------------------------------------------------------------------

    def handover_diagnostics_by_objective(self, objective_type: str = "Mission") -> str:
        """Count, rate, and average transition time for handovers by objective."""
        occurrence_query = self.robot_handover(objective_type)
        s = self.s
        obs_time_expr = s.seconds_between("objectiveStart", "objectiveEnd")
        return f"""
              CALL {{{_indent(occurrence_query, 2)}}}
              WITH objective, count(*) AS count, avg(transitionTime) AS avgTransitionTime
              MATCH {s.entity('objective')}<-{s.rel(s.corr_rel)}-{s.event('e')}
              WITH objective, count, avgTransitionTime,
                min(e.{s.event_start_prop}) AS objectiveStart,
                max(e.{s.event_end_prop}) AS objectiveEnd
              WITH objective, count, avgTransitionTime, {obs_time_expr} AS obsTime
              RETURN objective, count, avgTransitionTime, obsTime,
                CASE WHEN obsTime = 0 THEN null ELSE toFloat(count) / obsTime END AS rate
              ORDER BY count DESC
              """.strip()

    def switch_diagnostics_by_robot(self, objective_type: str = "Mission") -> str:
        """Count, rate, and average switch time for objective switches by robot."""
        occurrence_query = self.objective_switch(objective_type)
        s = self.s
        event_duration = s.seconds_between(f"e.{s.event_start_prop}", f"e.{s.event_end_prop}")
        return f"""
            CALL {{{_indent(occurrence_query, 2)}}}
            WITH robot, count(*) AS count, avg(switchTime) AS avgSwitchTime
            MATCH {s.entity('robot')}<-{s.rel(s.corr_rel)}-{s.event('e')}
            WITH robot, count, avgSwitchTime, sum({event_duration}) AS activeTime
            RETURN robot, count, avgSwitchTime, activeTime,
              CASE WHEN activeTime = 0 THEN null ELSE toFloat(count) / activeTime END AS rate
            ORDER BY count DESC
            """.strip()

    def cap_return_diagnostics_by_capability(self, objective_type: str = "Mission") -> str:
        """Count, pressure, and average return time by capability."""
        occurrence_query = self.capability_driven_return(objective_type)
        s = self.s
        return f"""
              CALL {{  {_indent(occurrence_query, 2)} }}
              UNWIND capabilities AS capability
              WITH capability, count(*) AS capReturnCount, avg(returnTime) AS avgReturnTime
              MATCH {s.entity('ro')}-{s.rel(s.has_rel)}->(capability)
              WHERE {s.type_filter('ro', 'Robot')}
              WITH capability, capReturnCount, avgReturnTime, count(DISTINCT ro) AS availability
              RETURN capability, capReturnCount, availability,
                CASE WHEN availability = 0 THEN null ELSE toFloat(capReturnCount) / availability END AS capPressure,
                avgReturnTime
              ORDER BY capPressure DESC, capReturnCount DESC
              """.strip()

    def all_occurrence_queries(self, objective_type: str = "Mission") -> Dict[str, str]:
        """Return all pattern occurrence queries as a dictionary."""
        return {
            f"handover_{objective_type.lower()}": self.robot_handover(objective_type),
            f"co_participation_{objective_type.lower()}": self.co_participation(objective_type),
            f"objective_switch_{objective_type.lower()}": self.objective_switch(objective_type),
            f"capability_driven_return_{objective_type.lower()}": self.capability_driven_return(objective_type),
            "parallel_collaboration_mission": self.parallel_collaboration_mission(),
            "parallel_collaboration_segment": self.parallel_collaboration_segment(),
        }

    def all_diagnostic_queries(self, objective_type: str = "Mission") -> Dict[str, str]:
        """Return selected aggregated diagnostic queries as a dictionary."""
        return {
            f"handover_diagnostics_by_{objective_type.lower()}": self.handover_diagnostics_by_objective(objective_type),
            f"switch_diagnostics_by_robot_{objective_type.lower()}": self.switch_diagnostics_by_robot(objective_type),
            f"cap_return_diagnostics_by_capability_{objective_type.lower()}": self.cap_return_diagnostics_by_capability(objective_type),
            "sync_diagnostics_parallel_segments": self.synchronization_diagnostics_parallel_segments(),
        }


class Neo4jRunner:
    """Small optional wrapper for executing the generated queries.

    Install with: pip install neo4j
    """

    def __init__(self, uri: str, user: str, password: str, database: Optional[str] = None) -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:  
            raise ImportError("Install the Neo4j driver with: pip install neo4j") from exc

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    def close(self) -> None:
        self._driver.close()

    def run(self, query: str, parameters: Optional[Mapping[str, Any]] = None) -> list[dict[str, Any]]:
        with self._driver.session(database=self._database) as session:
            result = session.run(query, parameters or {})
            return [record.data() for record in result]

    def run_many(self, queries: Mapping[str, str]) -> Dict[str, list[dict[str, Any]]]:
        return {name: self.run(query) for name, query in queries.items()}


# ----------------------------------------------------------------------
# Simple CLI: print the generated queries.
# ----------------------------------------------------------------------


def _indent(text: str, spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(pad + line if line.strip() else line for line in text.splitlines())


def print_queries(queries: Mapping[str, str]) -> None:
    for name, query in queries.items():
        print("\n" + "=" * 88)
        print(name)
        print("=" * 88)
        print(query)


if __name__ == "__main__":
    factory = CollaborationPatternCypher()
    print_queries(factory.all_occurrence_queries("Mission"))
    print_queries(factory.all_occurrence_queries("Segment"))
