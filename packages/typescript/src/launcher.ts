/** SDK-selected executable entry; the author module is imported after admission. */
import {serveSelectedApplication} from './selected-application.js';

const [configuration, runtime] = process.argv.slice(2).map(value=>Number(value));
if (configuration === undefined || runtime === undefined || !Number.isSafeInteger(configuration)
  || !Number.isSafeInteger(runtime) || configuration < 0 || runtime < 0 || configuration === runtime) throw new Error('Selected application requires distinct inherited descriptors');
await serveSelectedApplication(configuration, runtime);
