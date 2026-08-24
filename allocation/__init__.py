from .random_alloc import RandomAllocator
from .greedy import GreedyAllocator
from .auction import AuctionAllocator
from .hungarian import HungarianAllocator

ALLOCATORS = {
    "random": RandomAllocator,
    "greedy": GreedyAllocator,
    "auction": AuctionAllocator,
    "hungarian": HungarianAllocator,
}
