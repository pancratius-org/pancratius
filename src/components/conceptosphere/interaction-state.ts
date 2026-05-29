export interface InteractionSnapshot {
  hovered: string | null;
  pinned: string | null;
  filteredCommunities: ReadonlySet<number>;
  search: string;
}

export class GraphInteractionState {
  private hoveredNode: string | null = null;
  private pinnedNode: string | null = null;
  private readonly communityFilters = new Set<number>();
  private searchText = "";

  get hovered(): string | null {
    return this.hoveredNode;
  }

  get pinned(): string | null {
    return this.pinnedNode;
  }

  get search(): string {
    return this.searchText;
  }

  get hasCommunityFilter(): boolean {
    return this.communityFilters.size > 0;
  }

  hasCommunity(communityId: number): boolean {
    return this.communityFilters.has(communityId);
  }

  hover(nodeId: string | null): void {
    this.hoveredNode = nodeId;
  }

  pin(nodeId: string): void {
    this.pinnedNode = nodeId;
    this.hoveredNode = nodeId;
  }

  clearFocus(): void {
    this.pinnedNode = null;
    this.hoveredNode = null;
  }

  setSearch(value: string): void {
    this.searchText = value.trim();
  }

  toggleCommunity(communityId: number): void {
    if (this.communityFilters.has(communityId)) this.communityFilters.delete(communityId);
    else this.communityFilters.add(communityId);
  }

  clearCommunityFilter(): void {
    this.communityFilters.clear();
  }

  clearAll(): void {
    this.clearFocus();
    this.clearCommunityFilter();
    this.searchText = "";
  }

  snapshot(): InteractionSnapshot {
    return {
      hovered: this.hoveredNode,
      pinned: this.pinnedNode,
      filteredCommunities: new Set(this.communityFilters),
      search: this.searchText,
    };
  }
}
