import { createContext, type Dispatch, type SetStateAction } from 'react';

// Routes can supply their own list inside the shared, resizable Sidebar.
// The shell continues to own navigation, the mobile drawer and WorkspaceRail.
export const SidebarPanelContext = createContext<
  [HTMLElement | null, Dispatch<SetStateAction<HTMLElement | null>>] | undefined
>(undefined);
