from dataclasses import dataclass
from typing import Dict

import torch as t
from jaxtyping import Float, Int
from typeguard import typechecked


# Initialise cache for routing and use for MoE layers
@dataclass
class MoELayerCache:
    """G: softmaxed routing weights for the top k experts
    token_assignments: the top k expert ids
    routing_weights: raw outputs of the routing model (before softmax)
    """

    G: Float[t.Tensor, "k num_experts"]
    token_assignments: Int[t.Tensor, "k num_experts"]
    routing_weights: Float[t.Tensor, "batch*seq num_experts"]


# @typechecked
class MoEFullCache(Dict[str, MoELayerCache]):
    def __init__(self, moe_cache_dict: Dict[str, MoELayerCache]):
        super().__init__(moe_cache_dict)

    def __setitem__(self, idx: str, cache: MoELayerCache) -> None:
        assert isinstance(cache, MoELayerCache)
        return super().__setitem__(idx, cache)

    @property
    def G(self) -> Float[t.Tensor, "layer k num_experts"]:
        return t.stack([cache.G for idx, cache in self.items()], dim=0)

    @property
    def token_assignments(self) -> Int[t.Tensor, "layer k num_experts"]:
        return t.stack([cache.token_assignments for idx, cache in self.items()], dim=0)

    @property
    def routing_weights_tensor(self) -> Float[t.Tensor, "layer batch*seq num_experts"]:
        return t.stack([cache.routing_weights for idx, cache in self.items()], dim=0)

    @property
    def layer_indices(self) -> list[str]:
        return list(self.keys())
