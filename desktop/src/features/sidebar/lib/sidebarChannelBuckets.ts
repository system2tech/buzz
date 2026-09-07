import * as React from "react";
import type { Channel } from "@/shared/api/types";
import type { ChannelSection } from "./useChannelSections";
import {
  sectionSortGroupKey,
  sortChannelsForSidebar,
  type ChannelSortGroupKey,
  type ChannelSortMode,
} from "./channelSortPreference";

/** Keep automatic manager groups out of manual and ordinary channel lists. */
export function sidebarChannelBuckets(
  channels: Channel[],
  sections: ChannelSection[],
  assignments: Record<string, string>,
  starredIds: ReadonlySet<string> | undefined,
  groupedIds: ReadonlySet<string>,
  sortModeFor: (group: ChannelSortGroupKey) => ChannelSortMode,
) {
  const bySection: Record<string, Channel[]> = {};
  const unassigned: Channel[] = [];
  const sectionIds = new Set(sections.map(({ id }) => id));
  for (const channel of channels) {
    if (starredIds?.has(channel.id) || groupedIds.has(channel.id)) continue;
    const sectionId = assignments[channel.id];
    if (sectionId && sectionIds.has(sectionId)) {
      bySection[sectionId] ??= [];
      bySection[sectionId].push(channel);
    } else unassigned.push(channel);
  }
  for (const sectionId of Object.keys(bySection)) {
    bySection[sectionId] = sortChannelsForSidebar(
      bySection[sectionId],
      sortModeFor(sectionSortGroupKey(sectionId)),
    );
  }
  return {
    bySection,
    unassigned: sortChannelsForSidebar(unassigned, sortModeFor("channels")),
  };
}

/** Memoize manual buckets independently from status ticks and channel activity. */
export function useSidebarChannelBuckets(
  ...args: Parameters<typeof sidebarChannelBuckets>
) {
  const [channels, sections, assignments, starredIds, groupedIds, sortModeFor] =
    args;
  return React.useMemo(
    () =>
      sidebarChannelBuckets(
        channels,
        sections,
        assignments,
        starredIds,
        groupedIds,
        sortModeFor,
      ),
    [channels, sections, assignments, starredIds, groupedIds, sortModeFor],
  );
}
