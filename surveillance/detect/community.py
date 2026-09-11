"""Community detection over the inferred trading graphs.

Louvain is used (via networkx's built-in ``louvain_communities``) for three reasons that
matter here and are worth being able to defend:

* **It does not need the number of communities up front.** Spectral clustering and k-means
  do. Nobody knows how many wash rings are operating this week, and guessing wrong either
  merges distinct rings or shatters one.
* **It has a resolution parameter.** Modularity has a documented resolution limit: below a
  certain size relative to the graph, real communities get absorbed into larger ones. Rings
  here are three to five accounts in graphs of hundreds, which is exactly the regime the
  resolution limit bites in, so being able to turn that dial is not optional.
* **It is fast enough to sweep.** A parameter nobody can afford to vary is a parameter
  nobody has justified.

The known weakness, stated honestly: Louvain can produce internally disconnected
communities. Leiden (``leidenalg``) fixes this and would be the upgrade for a production
system; it is an extra native dependency, and we mitigate here by running Louvain on a
graph already filtered to strong edges and then re-checking connectivity, which catches the
pathological case without the extra dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx
import numpy as np
import pandas as pd

#: Louvain resolution, chosen by sweep rather than by argument -- and the sweep contradicted
#: the argument. The intuition was that a three-to-five account ring is a small community,
#: so resolution above 1.0 (which favours smaller communities) should suit it. Measured, the
#: opposite holds: 0.6-1.0 recovers every planted ring, 1.4 recovers 83%, and 1.8 collapses
#: to 17% at no gain in precision.
#:
#: The mechanism, in hindsight: the filtered graph is already sparse, so pushing resolution
#: up does not sharpen a ring out of surrounding noise -- there is no surrounding noise
#: left. It fragments the ring itself, and the pieces fall below the minimum member count.
#:
#: Full sweep in `docs/design-rationale.md`.
DEFAULT_RESOLUTION = 1.0
DEFAULT_SEED = 11


@dataclass(slots=True)
class Candidate:
    """A group of accounts the graph says are unusually connected in one security."""

    security_id: int
    members: tuple[int, ...]
    edges: pd.DataFrame
    kind: str  # "reciprocal" | "same_side"
    window_start: object = None
    window_end: object = None
    metrics: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.members)


def _communities(graph: nx.Graph, resolution: float, seed: int) -> list[set[int]]:
    if graph.number_of_edges() == 0:
        return []
    found = nx.community.louvain_communities(
        graph, weight="weight", resolution=resolution, seed=seed
    )
    # Louvain's documented failure mode: a returned community need not be internally
    # connected. Split any that are not, rather than reporting a "ring" whose members have
    # no path to each other.
    out: list[set[int]] = []
    for community in found:
        sub = graph.subgraph(community)
        out.extend(nx.connected_components(sub))
    return out


def find_candidates(
    pairs: pd.DataFrame,
    *,
    kind: str,
    min_members: int = 3,
    min_events: int = 4,
    min_lift: float = 20.0,
    min_reciprocity: float = 0.3,
    method: str = "louvain",
    resolution: float = DEFAULT_RESOLUTION,
    seed: int = DEFAULT_SEED,
) -> list[Candidate]:
    """Filter edges, then find communities within each security.

    Filtering before clustering is deliberate. Louvain partitions *everything* it is given,
    so handing it the raw graph returns a tidy partition of ordinary trading and every
    community looks like a finding. The filter is what makes a community mean something:
    only edges that are both statistically surprising (lift) and structurally consistent
    with the typology (reciprocity, for rings) survive to be clustered.

    ``method`` differs by typology, and the reason is structural rather than cosmetic.

    A **ring** hides inside a security's ordinary trading: its members also trade that name
    with everyone else, so the filtered graph is still a connected mass and the job is to
    find the tight sub-structure within it. That is what modularity optimisation is for.

    A **coordinated cluster** is the opposite. The lift filter is already doing the
    separation -- a co-trade three orders of magnitude above chance in a name neither
    account touches is not ordinary flow -- so the surviving edges *are* the cluster.
    Running Louvain over them then does active harm: measured, resolution 1.4 shattered
    four of five real clusters below the minimum member count, reporting nothing where the
    edges plainly described a fifteen-account group. Connected components is the honest
    primitive once the filter has done its work.
    """
    if pairs.empty:
        return []

    if kind == "reciprocal":
        mask = (
            (pairs["opposite_total"] >= min_events)
            & (pairs["reciprocity"] >= min_reciprocity)
            & (pairs["opposite_lift"] >= min_lift)
        )
        weight_col = "reciprocity"
    else:
        mask = (pairs["same_side"] >= min_events) & (pairs["same_lift"] >= min_lift)
        weight_col = "same_lift"

    strong = pairs[mask]
    if strong.empty:
        return []

    out: list[Candidate] = []
    for security_id, g in strong.groupby("security_id", sort=False):
        graph = nx.Graph()
        for row in g.itertuples(index=False):
            w = float(getattr(row, weight_col))
            if weight_col == "same_lift":
                # Compress a heavy-tailed weight so one extreme pair cannot dominate the
                # modularity optimisation for the whole security.
                w = float(np.log1p(w))
            graph.add_edge(int(row.a), int(row.b), weight=max(w, 1e-6))

        communities = (
            _communities(graph, resolution, seed)
            if method == "louvain"
            else list(nx.connected_components(graph))
        )
        for community in communities:
            if len(community) < min_members:
                continue
            members = tuple(sorted(int(x) for x in community))
            member_set = set(members)
            edges = g[g["a"].isin(member_set) & g["b"].isin(member_set)]
            if edges.empty:
                continue
            out.append(
                Candidate(
                    security_id=int(security_id),
                    members=members,
                    edges=edges,
                    kind=kind,
                    window_start=edges["first_seen"].min(),
                    window_end=edges["last_seen"].max(),
                    metrics={
                        "n_edges": int(len(edges)),
                        "density": float(
                            2 * len(edges) / (len(members) * (len(members) - 1))
                        ),
                        "median_lift": float(
                            edges["opposite_lift" if kind == "reciprocal" else "same_lift"].median()
                        ),
                        "median_reciprocity": float(edges["reciprocity"].median()),
                    },
                )
            )
    return out
