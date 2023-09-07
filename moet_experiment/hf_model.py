from json import load
from typing import Optional

import torch as t
from einops import einsum, rearrange, reduce
from torch import nn
from torch.nn import functional as F
from transformers import PretrainedConfig, PreTrainedModel

from general import device
from mixture_of_experts.cache import TokenChoiceFullCache
from moet_experiment.model import MoET


def load_balancing_aux_loss_function(moe_cache: TokenChoiceFullCache) -> float:
    """Load balancing auxiliary loss.

    Reference: Shazeer et al (2017) and ST-MoE: Designing Stable and Transferable Sparse Expert Models, https://arxiv.org/pdf/2202.08906.pdf

    Parameters
    ----------
    moe_cache : MoEFullCache
        MoE cache containing G, assignments and routing logits

    Returns
    -------
    float
        Load balancing auxiliary loss
    """
    num_experts = moe_cache.num_experts
    num_tokens = moe_cache.num_tokens

    total_tokens_per_expert = reduce(moe_cache.P, "layer expert batch_seq k -> layer expert", "sum")  # [layer, expert]
    frac_tokens_per_expert = total_tokens_per_expert / num_tokens

    routing_probs = F.softmax(moe_cache.routing_weights_tensor, dim=-1)  # [layer, num_experts, batch_seq]

    total_router_prob_per_expert = reduce(routing_probs, "layer num_experts batch_seq -> layer num_experts", "sum")  # [layer, num_experts]
    frac_router_prob_per_expert = total_router_prob_per_expert / num_tokens

    # Dot product
    lb_loss = num_experts * einsum(frac_tokens_per_expert, frac_router_prob_per_expert, "layer expert, layer expert ->")

    return lb_loss.item()

def router_z_loss_function(moe_cache: TokenChoiceFullCache) -> float:
    """Router z loss.

    Reference: ST-MoE: Designing Stable and Transferable Sparse Expert Models, https://arxiv.org/pdf/2202.08906.pdf

    Note that we've chosen not to multiply divide through the num_experts here.

    Parameters
    ----------
    moe_cache : MoEFullCache
        MoE cache containing G, assignments and routing logits

    Returns
    -------
    float
        Router z loss
    """
    router_logits = moe_cache.routing_weights_tensor # [layer, num_experts, batch_seq]

    lse_logits = t.logsumexp(router_logits, dim=-1)  # [layer, num_experts]
    squared_lse_logits = lse_logits ** 2

    z_loss = einsum(squared_lse_logits, "layer num_experts ->") / (moe_cache.num_tokens)

    return z_loss.item()

def expert_importance_loss(moe_cache: TokenChoiceFullCache) -> float:
    """Load balancing auxiliary loss for Experts based on balancing expert importance.
    Square of standard deviation of expert importance across tokens divided by the mean expert importance.

    Reference: Residual Mixture of Experts, https://arxiv.org/pdf/2204.09636.pdf

    Parameters
    ----------
    moe_cache : TokenChoiceFullCache
        _description_

    Returns
    -------
    float
        _description_
    """
    routing_weights = moe_cache.routing_weights_tensor  # [layer, num_experts, batch_seq]
    routing_probs = F.softmax(routing_weights, dim=2)  # [layer, num_experts, batch_seq]

    expert_importance = reduce(routing_probs, "layer num_experts batch_seq -> layer num_experts", "sum")  # [layer, num_experts]
    flat_expert_importance = rearrange(expert_importance, "layer num_experts -> (layer num_experts)")

    std_expert_importance = t.std(flat_expert_importance)
    mean_expert_importance = t.mean(flat_expert_importance)

    expert_importance_loss = (std_expert_importance / mean_expert_importance) ** 2

    return expert_importance_loss.item()

def local_entropy_loss(moe_cache: TokenChoiceFullCache) -> float:
    """Expert load balancing loss introduced in the LIMOE paper. Used in combination with the global entropy loss.

    This pushes each tokens towards choosing a single expert more strongly rather than being indifferent between experts.

    Reference: https://arxiv.org/pdf/2206.02770.pdf

    Parameters
    ----------
    moe_cache : TokenChoiceFullCache
        _description_

    Returns
    -------
    float
        _description_
    """
    routing_weights = moe_cache.routing_weights_tensor  # [layer, num_experts, batch_seq]
    routing_probs = F.softmax(routing_weights, dim=2)  # [layer, num_experts, batch_seq]
    flat_routing_probs = rearrange(routing_probs, "layer num_experts batch_seq -> (layer num_experts) batch_seq") # layer_expert, batch_seq

    # Calculate the entropy, denoted h in the paper
    local_entropy = - t.sum(flat_routing_probs * t.log(flat_routing_probs), dim=0)  # [batch_seq]

    local_entropy_loss = t.mean(local_entropy)

    return local_entropy_loss.item()


def global_entropy_loss(moe_cache: TokenChoiceFullCache) -> float:
    """Expert load balancing loss introduced in the LIMOE paper. Used in combination with the local entropy loss.

    To combat the issue of the local entropy loss pushing the model towards a single expert (which may all be the same expert!!), we add a global entropy loss which pushes the model towards a uniform distribution over experts.

    From the paper:
    Intuitively, it is desirable for text tokens to use multiple experts, but not all of them. In order to allow flexibility, we threshold the global entropy loss as Ωτglobal(Gm) = max{0, τ + Ωglobal(Gm)}, such that the model is encouraged to have a certain minimum entropy, but after exceeding that, the loss is not applied. This avoids distributional collapse but does not apply overly restrictive priors on the routing distribution, as there are many optimal solutions. This can be thought of as a “soft minimum” S. With τ = log(S), the model must use at least S experts to minimize the loss (either a uniform distribution across S experts -with entropy log(S)-, or a non-uniform distribution using more than S).

    Reference: https://arxiv.org/pdf/2206.02770.pdf

    Parameters
    ----------
    moe_cache : TokenChoiceFullCache
        _description_

    Returns
    -------
    float
        _description_
    """
    routing_weights = moe_cache.routing_weights_tensor  # [layer, num_experts, batch_seq]
    routing_probs = F.softmax(routing_weights, dim=2)  # [layer, num_experts, batch_seq]
    flat_routing_probs = rearrange(routing_probs, "layer num_experts batch_seq -> (layer num_experts) batch_seq") # layer_expert, batch_seq

    global_routing_probs = t.mean(flat_routing_probs, dim=1)  # [layer_expert]

    # Calculate the entropy, denoted h in the paper
    global_entropy_loss = - t.sum(global_routing_probs * t.log(global_routing_probs), dim=0)

    return global_entropy_loss.item()


class MoETHFConfig(PretrainedConfig):

    def __init__(
        self,
        block_type="MoE",
        layers: int = 8,
        **kwargs,
    ):

        self.block_type = block_type
        self.layers = layers
        super().__init__(**kwargs)


class MoET_hf(PreTrainedModel):
    def __init__(self, hf_config: MoETHFConfig = MoETHFConfig()):
        super().__init__(hf_config)
        self.hf_config = hf_config

        self.model = MoET()

        self.lb_coef = self.model.config.lb_coef
        self.z_coef = self.model.config.z_coef

    def forward(self, input_ids: t.Tensor, attention_mask: t.Tensor, return_loss: bool = True, **kwargs):
        """Forward function for hf wrapped model.

        Parameters
        ----------
        input_ids : Int[t.Tensor, "batch_size, seq_len"]
            Input tokens
        attention_mask : t.Tensor
            Attention mask
        return_loss : bool, optional
            Whether to return the model's loss in the output, by default True

        Returns
        -------
        dict
            Output dict
        """
        # Forward pass
        logits, moe_cache = self.model(input_ids, attention_mask)

        if return_loss:
            labels = input_ids[:, 1:]
            pred_logits = logits[:, :-1, :]

            flattened_logits = rearrange(pred_logits, "b s v -> (b s) v")
            flattened_labels = rearrange(labels, "b s -> (b s)")

            cross_entropy_loss = F.cross_entropy(flattened_logits, flattened_labels)

            load_balancing_aux_loss = load_balancing_aux_loss_function(moe_cache)
            router_z_loss = router_z_loss_function(moe_cache)

            loss = cross_entropy_loss + self.lb_coef * load_balancing_aux_loss + self.z_coef * router_z_loss

            return {"loss": loss, "cross_entropy_loss": cross_entropy_loss,
                    "load_balancing_aux_loss": load_balancing_aux_loss,
                    "router_z_loss": router_z_loss,
                    "logits": logits}
        else:
            return {"logits": logits}
