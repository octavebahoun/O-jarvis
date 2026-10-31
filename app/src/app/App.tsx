import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { RouterProvider } from "react-router";

import { SplashScreen } from "../components/SplashScreen";
import { EngineUnreachableError } from "../lib/engine/client";
import { EngineProvider } from "../lib/engine/EngineProvider";
import { router } from "./router";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      refetchOnWindowFocus: false,
      retry: (failures, error) => error instanceof EngineUnreachableError && failures < 3,
    },
  },
});

export function App() {
  const [showSplash, setShowSplash] = useState(true);

  return (
    <QueryClientProvider client={queryClient}>
      <EngineProvider>
        {showSplash && (
          <SplashScreen
            onComplete={() => {
              setShowSplash(false);
            }}
          />
        )}
        <RouterProvider router={router} />
      </EngineProvider>
    </QueryClientProvider>
  );
}
