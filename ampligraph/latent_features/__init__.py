"""This module includes neural graph embedding models and support functions.

Knowledge graph embedding models are neural architectures that encode concepts from a knowledge graph
(i.e. entities :math:`\mathcal{E}` and relation types :math:`\mathcal{R}`) into low-dimensional, continuous vectors
:math:`\in \mathcal{R}^k`. Such *knowledge graph embeddings* have applications in knowledge graph completion,
entity resolution, and link-based clustering, just to cite a few :cite:`nickel2016review`.

"""

from .models import EmbeddingModel, TransE, DistMult, ComplEx, HolE, RandomBaseline, MODEL_REGISTRY
from .loss_functions import Loss, AbsoluteMarginLoss, SelfAdversarialLoss, NLLLoss, PairwiseLoss,\
    LOSS_REGISTRY
from .regularizers import Regularizer, LPRegularizer, REGULARIZER_REGISTRY
from .misc import get_entity_triples
from .model_utils import save_model, restore_model


__all__ = ['LOSS_REGISTRY', 'REGULARIZER_REGISTRY', 'MODEL_REGISTRY',
           'EmbeddingModel', 'TransE', 'DistMult', 'ComplEx', 'HolE', 'RandomBaseline',
           'Loss', 'AbsoluteMarginLoss', 'SelfAdversarialLoss', 'NLLLoss', 'PairwiseLoss', 
           'Regularizer', 'LPRegularizer', 'get_entity_triples','save_model', 'restore_model']


