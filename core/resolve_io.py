import sys
import os

# Configuramos las variables de entorno para que el script externo encuentre a DaVinci Resolve en Mac
os.environ["RESOLVE_SCRIPT_API"] = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting"
os.environ["RESOLVE_SCRIPT_LIB"] = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
python_path_resolve = "/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting/Modules"
if python_path_resolve not in sys.path:
    sys.path.append(python_path_resolve)

class ResolveIO:
    def __init__(self):
        self.resolve = self._get_resolve()
        self.project_manager = self.resolve.GetProjectManager() if self.resolve else None
        self.project = self.project_manager.GetCurrentProject() if self.project_manager else None
        self.media_pool = self.project.GetMediaPool() if self.project else None
        
    def _get_resolve(self):
        try:
            import importlib.util
            ext = ".so"
            path = "/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Libraries/Fusion/fusionscript.so"
            if not os.path.exists(path):
                return None
            spec = importlib.util.spec_from_file_location("fusionscript", path)
            bmd = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(bmd)
            return bmd.scriptapp("Resolve")
        except Exception as e:
            print(f"Error cargando Resolve API: {e}")
            return None

    def get_video_clips(self):
        """Retorna una lista de diccionarios con info de los clips de video disponibles"""
        if not self.project:
            return []
            
        timeline = self.project.GetCurrentTimeline()
        clips_data = []
        
        if timeline:
            # Get clips from V1
            items = timeline.GetItemListInTrack("video", 1)
            if items:
                for item in items:
                    media_item = item.GetMediaPoolItem()
                    if media_item:
                        file_path = media_item.GetClipProperty("File Path")
                        if file_path:
                            clips_data.append({
                                "name": f"V1: {item.GetName()}",
                                "path": file_path,
                                "item": media_item
                            })
        
        if not clips_data and self.media_pool:
            # Fallback to media pool root folder
            root = self.media_pool.GetRootFolder()
            if root:
                for item in root.GetClipList():
                    if item.GetClipProperty("Type") == "Video":
                        path = item.GetClipProperty("File Path")
                        if path:
                            clips_data.append({
                                "name": f"MediaPool: {item.GetName()}",
                                "path": path,
                                "item": item
                            })
                            
        # Deduplicar
        unique_clips = []
        seen_paths = set()
        for c in clips_data:
            if c["path"] not in seen_paths:
                unique_clips.append(c)
                seen_paths.add(c["path"])
                
        return unique_clips

    def create_timeline(self, name):
        if not self.media_pool:
            return None
        return self.media_pool.CreateEmptyTimeline(name)
        
    def get_timeline_framerate(self):
        if not self.project: return 24.0
        tl = self.project.GetCurrentTimeline()
        if tl:
            fr = tl.GetSetting("timelineFrameRate")
            try:
                return float(fr)
            except:
                return 24.0
        return 24.0

    def get_timeline_start_frame(self):
        if not self.project: return 86400
        tl = self.project.GetCurrentTimeline()
        if tl:
            return tl.GetStartFrame()
        return 86400

    def append_to_timeline(self, append_data):
        if not self.media_pool:
            return []
        return self.media_pool.AppendToTimeline(append_data)
        
    def apply_aesthetic_zooms(self, timeline_items):
        if not timeline_items: return
        
        zoom_values = [1.0, 1.15]
        for i, item in enumerate(timeline_items):
            zoom = zoom_values[i % len(zoom_values)]
            item.SetProperty("ZoomX", zoom)
            item.SetProperty("ZoomY", zoom)

    def delete_markers_by_custom_data(self, custom_data):
        if not self.project: return False
        tl = self.project.GetCurrentTimeline()
        if tl:
            return tl.DeleteMarkerByCustomData(custom_data)
        return False

    def add_marker_to_current_timeline(self, frameId, color, name, note, duration, customData):
        if not self.project: return False
        tl = self.project.GetCurrentTimeline()
        if tl:
            return tl.AddMarker(frameId, color, name, note, duration, customData)
        return False

    def get_timeline_items_for_path(self, file_path):
        """Returns a list of all V1 timeline items pointing to the given file path, sorted by timeline position."""
        if not self.project: return []
        tl = self.project.GetCurrentTimeline()
        found_items = []
        if tl:
            items = tl.GetItemListInTrack("video", 1)
            if items:
                for item in items:
                    mi = item.GetMediaPoolItem()
                    if mi and mi.GetClipProperty("File Path") == file_path:
                        found_items.append(item)
        # Sort by their start frame on the timeline to ensure chronological order
        found_items.sort(key=lambda x: x.GetStart())
        return found_items

    def source_sec_to_frame_timeline(self, items, source_sec, fps_timeline):
        """Converts a second in the source clip to an absolute frame number in the timeline, across multiple clips."""
        if not items:
            return 0
            
        target_f = round(source_sec * fps_timeline)
        
        for item in items:
            start_src = item.GetLeftOffset()
            end_src = start_src + item.GetDuration()
            
            if start_src <= target_f < end_src:
                return item.GetStart() + (target_f - start_src)
                
            # If the frame falls in a gap before this item (i.e. removed silence),
            # snap to the start of this item.
            if target_f < start_src:
                return item.GetStart()
                
        # If it's beyond the last item, return the end of the last item
        last_item = items[-1]
        return last_item.GetStart() + last_item.GetDuration()

    def frame_timeline_to_source_sec(self, items, frame_abs, fps_timeline):
        """Converts an absolute frame number in the timeline back to a second in the source clip."""
        if not items:
            return 0
            
        for item in items:
            if item.GetStart() <= frame_abs < item.GetStart() + item.GetDuration():
                frame_in_source = frame_abs - item.GetStart() + item.GetLeftOffset()
                return frame_in_source / fps_timeline
            
            # If for some reason the absolute frame falls before this item
            if frame_abs < item.GetStart():
                frame_in_source = item.GetLeftOffset()
                return frame_in_source / fps_timeline
                
        # If it's beyond the last item
        last_item = items[-1]
        frame_in_source = last_item.GetLeftOffset() + last_item.GetDuration()
        return frame_in_source / fps_timeline

    def absolute_frame_to_timecode(self, f_count, fps):
        """Converts an absolute frame number to a timecode string (HH:MM:SS:FF)"""
        h = int(f_count // (fps * 3600))
        m = int((f_count % (fps * 3600)) // (fps * 60))
        s = int((f_count % (fps * 60)) // fps)
        f = int(f_count % fps)
        return f"{h:02d}:{m:02d}:{s:02d}:{f:02d}"

    def source_second_to_timeline_timecode(self, item, source_second, fps):
        absolute_f = self.source_sec_to_frame_timeline(item, source_second, fps)
        return self.absolute_frame_to_timecode(absolute_f, fps)

    def absolute_frame_to_marker_frame(self, absolute_frame):
        if not self.project: return 0
        tl = self.project.GetCurrentTimeline()
        tl_start_frame = tl.GetStartFrame()
        # Marker frame is relative to timeline start
        return absolute_frame - tl_start_frame

    def apply_zooms_to_current_timeline(self, overwrite_zooms=False):
        if not self.project: return False
        tl = self.project.GetCurrentTimeline()
        if not tl: return False
        items = tl.GetItemListInTrack("video", 1)
        if not items: return False
        
        zoom_values = [1.0, 1.15]
        zoom_idx = 0
        for item in items:
            current_zoom_x = item.GetProperty("ZoomX")
            if current_zoom_x is not None and not overwrite_zooms and float(current_zoom_x) != 1.0:
                continue
            
            zoom = zoom_values[zoom_idx % len(zoom_values)]
            item.SetProperty("ZoomX", zoom)
            item.SetProperty("ZoomY", zoom)
            zoom_idx += 1
        return True

    def remove_zooms_from_current_timeline(self):
        if not self.project: return False
        tl = self.project.GetCurrentTimeline()
        if not tl: return False
        items = tl.GetItemListInTrack("video", 1)
        if not items: return False
        
        for item in items:
            item.SetProperty("ZoomX", 1.0)
            item.SetProperty("ZoomY", 1.0)
        return True
