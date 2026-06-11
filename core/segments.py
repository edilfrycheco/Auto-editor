class SegmentsBuilder:
    def __init__(self, fps=24.0, config=None):
        self.fps = fps
        self.config = config or {}
        padding = self.config.get("padding", {})
        self.pad_before_s = padding.get("pre_head_ms", 120) / 1000.0
        self.pad_after_s = padding.get("post_tail_ms", 180) / 1000.0
        self.max_silence_s = padding.get("max_silence_ms", 1000) / 1000.0
        self.remaining_silence_s = padding.get("remaining_silence_ms", 300) / 1000.0
        self.min_gap_s = 0.200

    def _get_discarded_intervals(self, align_results):
        discarded = []
        for group in align_results.get("retake_groups", []):
            best = group.get("best_take")
            for t in group.get("takes", []):
                if not best or t != best:
                    discarded.append((t["start_time"], t["end_time"]))
                    
        for seg in align_results.get("unmatched_segments", []):
            if seg.get("duplicate_action") == "CORTAR":
                discarded.append((seg["start_time"], seg["end_time"]))
        return discarded

    def _split_by_silence(self, item, transcript_words):
        # item has start_idx, end_idx, start_time, end_time
        start_idx = item.get("start_idx")
        end_idx = item.get("end_idx")
        
        if start_idx is None or end_idx is None:
            return [(item["start_time"], item["end_time"])]
            
        sub_segments = []
        current_sub_start = transcript_words[start_idx].get("start", item["start_time"])
        current_word_end = transcript_words[start_idx].get("end", current_sub_start)
        
        for i in range(start_idx + 1, end_idx + 1):
            w = transcript_words[i]
            w_start = w.get("start")
            w_end = w.get("end")
            
            if w_start is None or w_end is None:
                continue
                
            gap = w_start - current_word_end
            if gap > self.max_silence_s:
                # Cortar! Termina el subsegmento en current_word_end + remaining_silence_s
                sub_end = current_word_end + self.remaining_silence_s
                sub_segments.append((current_sub_start, sub_end))
                # Empieza uno nuevo restando el remaining silence al inicio de la siguiente palabra
                current_sub_start = w_start - self.remaining_silence_s
                
            current_word_end = w_end
            
        # Add the last sub_segment
        sub_segments.append((current_sub_start, current_word_end))
        return sub_segments

    def build_keep_segments(self, align_results, transcript_words):
        discarded_intervals = self._get_discarded_intervals(align_results)
        
        raw_segments = []
        for group in align_results.get("retake_groups", []):
            take = group.get("best_take")
            if take:
                subs = self._split_by_silence(take, transcript_words)
                raw_segments.extend([list(s) for s in subs])
                
        for seg in align_results.get("unmatched_segments", []):
            if seg.get("duplicate_action") == "CORTAR":
                continue
            subs = self._split_by_silence(seg, transcript_words)
            raw_segments.extend([list(s) for s in subs])
            
        if not raw_segments:
            return []
            
        raw_segments.sort(key=lambda x: x[0])
        
        # 1. Merge gaps before padding
        merged_raw = [raw_segments[0]]
        for current in raw_segments[1:]:
            previous = merged_raw[-1]
            gap = current[0] - previous[1]
            
            crosses_discarded = False
            for d_start, d_end in discarded_intervals:
                if max(previous[1], d_start) < min(current[0], d_end):
                    crosses_discarded = True
                    break
                    
            if gap <= self.min_gap_s and not crosses_discarded:
                previous[1] = max(previous[1], current[1])
            else:
                merged_raw.append(current)
                
        # 2. Apply padding with discarded midpoint caps
        padded_segments = []
        for i in range(len(merged_raw)):
            start, end = merged_raw[i][0], merged_raw[i][1]
            s = max(0.0, start - self.pad_before_s)
            e = end + self.pad_after_s
            
            # Cap pre-head
            for d_start, d_end in discarded_intervals:
                if d_start <= start and d_end > s:
                    midpoint = (d_start + d_end) / 2.0
                    s = max(s, midpoint)
                    
            # Cap post-tail
            for d_start, d_end in discarded_intervals:
                if d_end >= end and d_start < e:
                    midpoint = (d_start + d_end) / 2.0
                    e = min(e, midpoint)
                    
            padded_segments.append([s, e])
            
        # 3. Resolve collisions between conserved segments
        for i in range(len(padded_segments) - 1):
            curr = padded_segments[i]
            nxt = padded_segments[i+1]
            if curr[1] > nxt[0]:
                orig_end = merged_raw[i][1]
                orig_next_start = merged_raw[i+1][0]
                midpoint = (orig_end + orig_next_start) / 2.0
                curr[1] = min(curr[1], midpoint)
                nxt[0] = max(nxt[0], midpoint)
                
        # 4. Snap to frames
        frame_duration = 1.0 / self.fps
        final_segments = []
        for start, end in padded_segments:
            start_f = round(start / frame_duration)
            end_f = round(end / frame_duration)
            if end_f > start_f:
                final_segments.append({
                    "start_s": start_f * frame_duration,
                    "end_s": end_f * frame_duration,
                    "start_frame": start_f,
                    "end_frame": end_f
                })
            
        return final_segments
